import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetSidecar, setSidecar } from "./api-client";
import { streamEvents, type SseEvent } from "./sse";

/**
 * The sidecar's SSE wire format, parsed by hand.
 *
 * The native `EventSource` can't set an Authorization header, so this is a
 * `fetch` + a hand-rolled parser — which means every framing rule the browser
 * would have handled is ours to get right. The cases that bite:
 *
 *  - a chunk boundary can land anywhere, including mid-line and mid-UTF-8;
 *  - `event:` has to reset to `message` after each dispatch, or one named
 *    event relabels every anonymous one after it;
 *  - a stream that ends without a trailing blank line still has a pending
 *    event, and for a translate job that event is the terminal `done`.
 *
 * `fetch` is stubbed rather than mocked at the module level so the real
 * `ReadableStream` → `TextDecoderStream` → reader path under test actually
 * runs.
 */

function bodyFrom(chunks: string[] | Uint8Array[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(
          typeof chunk === "string" ? encoder.encode(chunk) : chunk,
        );
      }
      controller.close();
    },
  });
}

function respondWith(
  chunks: string[] | Uint8Array[],
  init: { ok?: boolean; status?: number; statusText?: string } = {},
) {
  const response = {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    statusText: init.statusText ?? "OK",
    body: init.ok === false ? null : bodyFrom(chunks),
  };
  const fetchMock = vi.fn(async () => response as unknown as Response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function collect(chunks: string[] | Uint8Array[]): Promise<SseEvent[]> {
  respondWith(chunks);
  const events: SseEvent[] = [];
  await streamEvents({ path: "/translate/j/events", onEvent: (e) => events.push(e) });
  return events;
}

beforeEach(() => {
  resetSidecar();
  setSidecar({ port: 54213, token: "tok-abc" });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("request shape", () => {
  it("addresses the sidecar on loopback with its bearer token", async () => {
    const fetchMock = respondWith(["event: done\ndata: {}\n\n"]);
    await streamEvents({ path: "/translate/j/events", onEvent: () => {} });

    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe("http://127.0.0.1:54213/translate/j/events");
    expect((init.headers as Record<string, string>).Authorization).toBe(
      "Bearer tok-abc",
    );
    expect((init.headers as Record<string, string>).Accept).toBe(
      "text/event-stream",
    );
  });

  it("rejects a non-ok response rather than reporting an empty stream", async () => {
    respondWith([], { ok: false, status: 404, statusText: "Not Found" });
    await expect(
      streamEvents({ path: "/translate/gone/events", onEvent: () => {} }),
    ).rejects.toThrow(/404/);
  });
});

describe("framing", () => {
  it("parses a named event with a JSON payload", async () => {
    expect(await collect(["event: progress\ndata: {\"percent\": 40}\n\n"])).toEqual([
      { type: "progress", data: { percent: 40 } },
    ]);
  });

  it("defaults an unnamed event to `message`", async () => {
    expect(await collect(['data: {"a": 1}\n\n'])).toEqual([
      { type: "message", data: { a: 1 } },
    ]);
  });

  it("resets the event type after each dispatch", async () => {
    /** Otherwise one `event: error` relabels every anonymous event after it. */
    const events = await collect([
      'event: progress\ndata: {"percent": 10}\n\n',
      'data: {"percent": 20}\n\n',
    ]);
    expect(events.map((e) => e.type)).toEqual(["progress", "message"]);
  });

  it("keeps the order events arrived in", async () => {
    const events = await collect([
      "event: progress\ndata: 1\n\n",
      "event: chunk_ready\ndata: 2\n\n",
      "event: done\ndata: 3\n\n",
    ]);
    expect(events.map((e) => e.type)).toEqual([
      "progress",
      "chunk_ready",
      "done",
    ]);
  });

  it("joins a multi-line data field with newlines", async () => {
    /** The spec's own rule, and how a traceback in an `error` payload arrives
     * when anything upstream splits it. Joining with "" would silently glue
     * the last word of one line to the first of the next. */
    const events = await collect(["event: error\ndata: line one\ndata: line two\n\n"]);
    expect(events[0].data).toBe("line one\nline two");
  });

  it("ignores comment lines", async () => {
    /** Proxies and keep-alives inject `: ping`; treating one as data would
     * dispatch a bogus event between two real ones. */
    const events = await collect([
      ": keep-alive\n\n",
      'event: done\ndata: {"ok": true}\n\n',
    ]);
    expect(events).toEqual([{ type: "done", data: { ok: true } }]);
  });

  it("tolerates CRLF line endings", async () => {
    const events = await collect(['event: done\r\ndata: {"ok": true}\r\n\r\n']);
    expect(events).toEqual([{ type: "done", data: { ok: true } }]);
  });

  it("ignores fields it has no use for", async () => {
    const events = await collect(['id: 7\nretry: 3000\nevent: done\ndata: {}\n\n']);
    expect(events).toEqual([{ type: "done", data: {} }]);
  });

  it("dispatches a trailing event that never got its blank line", async () => {
    /** A stream closed right after its terminal event — the overlay waits
     * forever on a `done` that was parsed but never delivered. */
    const events = await collect(['event: done\ndata: {"ok": true}\n']);
    expect(events).toEqual([{ type: "done", data: { ok: true } }]);
  });

  it("emits nothing for a blank separator with no data", async () => {
    expect(await collect(["\n\n", ": comment\n\n"])).toEqual([]);
  });

  it("emits nothing for an empty stream", async () => {
    expect(await collect([])).toEqual([]);
  });
});

describe("chunk boundaries", () => {
  it("reassembles an event split across reads", async () => {
    /** The network decides where chunks end. One event routinely arrives as
     * several reads, and a partial line must stay buffered. */
    const events = await collect([
      "event: prog",
      "ress\ndata: {\"per",
      'cent": 40}\n',
      "\n",
    ]);
    expect(events).toEqual([{ type: "progress", data: { percent: 40 } }]);
  });

  it("handles several events arriving in one read", async () => {
    const events = await collect([
      'event: progress\ndata: {"percent": 10}\n\nevent: done\ndata: {}\n\n',
    ]);
    expect(events.map((e) => e.type)).toEqual(["progress", "done"]);
  });

  it("reassembles a multi-byte character split across reads", async () => {
    /** `TextDecoderStream` is what makes this work; decoding each chunk
     * independently would corrupt Vietnamese text in a `paragraph_translated`
     * preview at every unlucky boundary. */
    const encoded = new TextEncoder().encode('data: {"t": "Xin chào"}\n\n');
    const split = encoded.indexOf(0xc3); // the first byte of "à"
    const events = await collect([encoded.slice(0, split + 1), encoded.slice(split + 1)]);
    expect(events[0].data).toEqual({ t: "Xin chào" });
  });
});

describe("payloads", () => {
  it("passes non-JSON data through as a string", async () => {
    expect(await collect(["event: note\ndata: just text\n\n"])).toEqual([
      { type: "note", data: "just text" },
    ]);
  });

  it("keeps a JSON array payload as an array", async () => {
    expect(await collect(["data: [1, 2, 3]\n\n"])).toEqual([
      { type: "message", data: [1, 2, 3] },
    ]);
  });

  it("preserves unicode in the payload", async () => {
    const events = await collect(['data: {"t": "Xin chào ⟦1⟧"}\n\n']);
    expect(events[0].data).toEqual({ t: "Xin chào ⟦1⟧" });
  });
});

describe("cancellation and errors", () => {
  function respondWithFailingBody(error: Error) {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.error(error);
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          ({
            ok: true,
            status: 200,
            statusText: "OK",
            body,
          }) as unknown as Response,
      ),
    );
  }

  it("ends quietly when the stream is aborted mid-read", async () => {
    /** The hooks abort their stream on unmount and on Cancel. Surfacing that
     * as an error would put a spurious toast on every navigation, and the
     * cancel path already has its own terminal `cancelled` event. */
    const abort = new Error("aborted");
    abort.name = "AbortError";
    respondWithFailingBody(abort);

    const onError = vi.fn();
    await expect(
      streamEvents({ path: "/x", onEvent: () => {}, onError }),
    ).resolves.toBeUndefined();
    expect(onError).not.toHaveBeenCalled();
  });

  it("reports a mid-stream read failure through onError and rethrows", async () => {
    /** Both halves matter: `onError` is what the hook turns into a toast, and
     * the rethrow is what stops the caller's `await` from looking like a
     * stream that ended normally. */
    respondWithFailingBody(new Error("connection reset"));

    const onError = vi.fn();
    await expect(
      streamEvents({ path: "/x", onEvent: () => {}, onError }),
    ).rejects.toThrow(/connection reset/);
    expect(onError).toHaveBeenCalledTimes(1);
  });

  it("lets an abort raised by fetch itself reject", async () => {
    /** The quiet path covers a failure *while reading*; an `AbortSignal` that
     * fires before the response arrives rejects out of `fetch`, outside that
     * handler. Asserted so the asymmetry is a decision, not a surprise. */
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        const err = new Error("aborted");
        err.name = "AbortError";
        throw err;
      }),
    );
    await expect(
      streamEvents({ path: "/x", onEvent: () => {} }),
    ).rejects.toThrow(/aborted/);
  });

  it("passes the abort signal down to fetch", async () => {
    const fetchMock = respondWith(["event: done\ndata: {}\n\n"]);
    const controller = new AbortController();
    await streamEvents({
      path: "/x",
      onEvent: () => {},
      signal: controller.signal,
    });
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.signal).toBe(controller.signal);
  });
});
