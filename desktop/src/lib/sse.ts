/**
 * SSE helper for the sidecar.
 *
 * The browser's native EventSource cannot send a custom Authorization header,
 * so we use `fetch` with a streamed response and parse the SSE wire format
 * by hand. This is what TanStack Query / @microsoft/fetch-event-source do.
 */

import { sidecarToken, sidecarUrl } from "./api-client";

export interface SseEvent<T = unknown> {
  type: string;
  /** The SSE `id:` field, when the server sent one. `api/jobs.py` sends the
   *  event's 1-based sequence number here on every real event — it's what a
   *  reattaching `streamJobEvents` caller sends back as `last_seq`. Reset
   *  per dispatch like `type`, not held sticky across events without one:
   *  this backend always sends `id` on real events, so the two behave
   *  identically here even though that's not full SSE-spec `id` persistence. */
  id?: string;
  data: T;
}

export interface SseStreamOptions<T> {
  path: string;
  onEvent: (event: SseEvent<T>) => void;
  onError?: (error: Error) => void;
  signal?: AbortSignal;
}

/** Open an SSE stream against the sidecar. Resolves when the stream ends. */
export async function streamEvents<T = unknown>(
  options: SseStreamOptions<T>,
): Promise<void> {
  const url = await sidecarUrl(options.path);
  const token = await sidecarToken();

  const response = await fetch(url, {
    method: "GET",
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "text/event-stream",
    },
    signal: options.signal,
  });

  if (!response.ok || !response.body) {
    throw new Error(
      `SSE request failed: ${response.status} ${response.statusText}`,
    );
  }

  const reader = response.body
    .pipeThrough(new TextDecoderStream())
    .getReader();

  let buffer = "";
  let eventType = "message";
  let eventId: string | undefined;
  let dataLines: string[] = [];

  const dispatch = () => {
    if (dataLines.length === 0) return;
    const raw = dataLines.join("\n");
    let parsed: T;
    try {
      parsed = JSON.parse(raw) as T;
    } catch {
      parsed = raw as unknown as T;
    }
    options.onEvent({ type: eventType, id: eventId, data: parsed });
    eventType = "message";
    eventId = undefined;
    dataLines = [];
  };

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        dispatch();
        break;
      }
      buffer += value;
      let newlineIdx: number;
      while ((newlineIdx = buffer.indexOf("\n")) !== -1) {
        const line = buffer.slice(0, newlineIdx).replace(/\r$/, "");
        buffer = buffer.slice(newlineIdx + 1);

        if (line === "") {
          dispatch();
          continue;
        }
        if (line.startsWith(":")) continue; // SSE comment / keep-alive
        if (line.startsWith("event:")) {
          eventType = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          dataLines.push(line.slice(5).trim());
        } else if (line.startsWith("id:")) {
          eventId = line.slice(3).trim();
        }
        // Other fields (e.g. `retry:`) are recognized but unused.
      }
    }
  } catch (err) {
    if ((err as Error).name === "AbortError") return;
    options.onError?.(err as Error);
    throw err;
  }
}

// ---------------------------------------------------------------------------
// streamJobEvents — a reconnecting wrapper for the job-registry SSE routes
// ---------------------------------------------------------------------------

/** Every job kind (`api/jobs.py`) ends in exactly one of these — confirmed by
 *  reading `_run_translation`, `_run_index` and `_run_ask`, each of which
 *  calls `job.finish(...)` with one of these three types on every code path
 *  (success, cancellation, generic failure). Seeing one means the stream
 *  ended for good; anything else means the connection merely dropped. */
const TERMINAL_EVENT_TYPES = new Set(["done", "error", "cancelled"]);

/** Consecutive reconnect attempts that delivered zero events before giving
 *  up. Resets whenever an attempt makes any progress, so a long job with a
 *  few brief blips doesn't hit a hard lifetime cap. */
const MAX_RECONNECT_ATTEMPTS = 5;
const RECONNECT_BASE_DELAY_MS = 500;
const RECONNECT_MAX_DELAY_MS = 8000;

function backoffMs(attempt: number): number {
  return Math.min(
    RECONNECT_BASE_DELAY_MS * 2 ** (attempt - 1),
    RECONNECT_MAX_DELAY_MS,
  );
}

function abortError(): Error {
  const err = new Error("Aborted");
  err.name = "AbortError";
  return err;
}

/** A `setTimeout` that rejects (AbortError) if `signal` fires first, instead
 *  of firing the full delay anyway — otherwise aborting mid-backoff would
 *  still make the caller wait out the whole delay before noticing. */
function abortableDelay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError());
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      reject(abortError());
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export interface StreamJobEventsOptions<T> {
  /** Builds the request path for one connection attempt, given the last
   *  event id observed so far (`null` on the first attempt) — e.g.
   *  `` `/translate/${jobId}/events${id ? `?last_seq=${id}` : ""}` ``. */
  buildPath: (lastEventId: string | null) => string;
  onEvent: (event: SseEvent<T>) => void;
  /** Fires before each reconnect attempt (not the first connection), with a
   *  1-based attempt number. Optional — nothing needs a UI hint yet, but the
   *  hook is there. */
  onReconnecting?: (attempt: number) => void;
  signal?: AbortSignal;
}

/**
 * Like `streamEvents`, but reopens the connection (replaying from the last
 * seen event id via `buildPath`) when it drops before a terminal event —
 * see `api/jobs.py`: a job now survives a disconnect and buffers its
 * history for exactly this. Resolves (never throws) once a terminal event
 * arrives, the signal aborts, or reconnect attempts are exhausted, so a
 * caller's existing "stream ended unexpectedly" fallback after the `await`
 * still fires as the last resort — this only makes a *transient* drop
 * invisible, not every failure mode.
 */
export async function streamJobEvents<T = unknown>(
  options: StreamJobEventsOptions<T>,
): Promise<void> {
  let lastEventId: string | null = null;
  let sawTerminal = false;
  let consecutiveEmptyAttempts = 0;

  while (true) {
    let receivedAny = false;
    try {
      await streamEvents<T>({
        path: options.buildPath(lastEventId),
        signal: options.signal,
        onEvent: (event) => {
          receivedAny = true;
          if (event.id !== undefined) lastEventId = event.id;
          if (TERMINAL_EVENT_TYPES.has(event.type)) sawTerminal = true;
          options.onEvent(event);
        },
      });
    } catch (err) {
      if ((err as Error).name === "AbortError") return;
      // A non-abort failure (network blip, sidecar hiccup) falls through to
      // the reconnect logic below — swallowed here, not rethrown.
    }

    if (sawTerminal || options.signal?.aborted) return;

    consecutiveEmptyAttempts = receivedAny ? 0 : consecutiveEmptyAttempts + 1;
    if (consecutiveEmptyAttempts > MAX_RECONNECT_ATTEMPTS) return;

    const attempt = consecutiveEmptyAttempts || 1;
    options.onReconnecting?.(attempt);
    try {
      await abortableDelay(backoffMs(attempt), options.signal);
    } catch {
      return; // aborted during backoff
    }
  }
}
