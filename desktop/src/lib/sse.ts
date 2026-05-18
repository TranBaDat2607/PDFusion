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
    options.onEvent({ type: eventType, data: parsed });
    eventType = "message";
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
        }
      }
    }
  } catch (err) {
    if ((err as Error).name === "AbortError") return;
    options.onError?.(err as Error);
    throw err;
  }
}
