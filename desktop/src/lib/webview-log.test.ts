import { afterEach, describe, expect, it, vi } from "vitest";

import {
  LOG_BUDGET,
  MAX_MESSAGE_CHARS,
  formatLogMessage,
  installWebviewLogging,
  type LogSink,
} from "./webview-log";

function setup(sinkOverrides: Partial<LogSink> = {}) {
  const originalWarn = vi.fn();
  const originalError = vi.fn();
  const console = { warn: originalWarn, error: originalError };
  const events = new EventTarget();
  let t = 1_000;
  const clock = {
    set: (v: number) => {
      t = v;
    },
  };
  const sink = { warn: vi.fn(), error: vi.fn(), ...sinkOverrides };
  const uninstall = installWebviewLogging(
    { console, events, now: () => t },
    sink,
  );
  return { console, events, sink, originalWarn, originalError, clock, uninstall };
}

const errorEvent = (init: { error?: unknown; message?: string }) =>
  Object.assign(new Event("error"), init);
const rejectionEvent = (reason: unknown) =>
  Object.assign(new Event("unhandledrejection"), { reason });

describe("formatLogMessage", () => {
  it("keeps a string as it is", () => {
    expect(formatLogMessage(["hello"])).toBe("hello");
  });

  it("uses an Error's stack when it has one", () => {
    const e = new Error("boom");
    e.stack = "Error: boom\n    at somewhere";
    expect(formatLogMessage([e])).toBe("Error: boom\n    at somewhere");
  });

  it("falls back to name and message when an Error has no stack", () => {
    const e = new TypeError("bad");
    e.stack = undefined;
    expect(formatLogMessage([e])).toBe("TypeError: bad");
  });

  it("JSON-stringifies other values", () => {
    expect(formatLogMessage([{ a: 1 }])).toBe('{"a":1}');
    expect(formatLogMessage([42])).toBe("42");
  });

  it("falls back to String() for a circular structure", () => {
    const o: Record<string, unknown> = {};
    o.self = o;
    expect(formatLogMessage([o])).toBe("[object Object]");
  });

  it("falls back to String() when JSON.stringify returns undefined", () => {
    expect(formatLogMessage([undefined])).toBe("undefined");
    expect(formatLogMessage([() => 1])).toBe(String(() => 1));
  });

  it("joins parts with a single space", () => {
    expect(formatLogMessage(["a", 1, "b"])).toBe("a 1 b");
  });

  it("cuts an over-long result to exactly MAX_MESSAGE_CHARS ending in an ellipsis", () => {
    const out = formatLogMessage(["x".repeat(MAX_MESSAGE_CHARS + 500)]);
    expect(out).toHaveLength(MAX_MESSAGE_CHARS);
    expect(out.endsWith("…")).toBe(true);
    expect(out.startsWith("x".repeat(MAX_MESSAGE_CHARS - 1))).toBe(true);
  });

  it("leaves a result of exactly MAX_MESSAGE_CHARS untouched", () => {
    const s = "y".repeat(MAX_MESSAGE_CHARS);
    expect(formatLogMessage([s])).toBe(s);
  });
});

describe("installWebviewLogging", () => {
  const uninstalls: Array<() => void> = [];
  afterEach(() => {
    uninstalls.splice(0).forEach((u) => u());
  });
  const make = (o: Partial<LogSink> = {}) => {
    const s = setup(o);
    uninstalls.push(s.uninstall);
    return s;
  };

  it("forwards console.warn to sink.warn", () => {
    const s = make();
    s.console.warn("careful", 1);
    expect(s.sink.warn).toHaveBeenCalledWith("careful 1");
    expect(s.sink.error).not.toHaveBeenCalled();
  });

  it("forwards console.error to sink.error", () => {
    const s = make();
    s.console.error("bad");
    expect(s.sink.error).toHaveBeenCalledWith("bad");
    expect(s.sink.warn).not.toHaveBeenCalled();
  });

  it("still calls the original console with the same arguments", () => {
    const s = make();
    const obj = { k: 1 };
    const originalWarn = s.originalWarn;
    const originalError = s.originalError;
    s.console.warn("w", obj);
    s.console.error("e", obj);
    expect(originalWarn).toHaveBeenCalledWith("w", obj);
    expect(originalError).toHaveBeenCalledWith("e", obj);
  });

  it("forwards an error event using its error", () => {
    const s = make();
    const e = new Error("kaput");
    e.stack = "Error: kaput\n    at x";
    s.events.dispatchEvent(errorEvent({ error: e, message: "ignored" }));
    expect(s.sink.error).toHaveBeenCalledWith("Error: kaput\n    at x");
  });

  it("forwards an error event's message when it has no error", () => {
    const s = make();
    s.events.dispatchEvent(errorEvent({ message: "Script error." }));
    expect(s.sink.error).toHaveBeenCalledWith("Script error.");
  });

  it("forwards an unhandled rejection with a prefix", () => {
    const s = make();
    s.events.dispatchEvent(rejectionEvent("nope"));
    expect(s.sink.error).toHaveBeenCalledWith("Unhandled rejection: nope");
  });

  it("formats a non-string rejection reason like any other value", () => {
    const s = make();
    s.events.dispatchEvent(rejectionEvent({ code: 7 }));
    expect(s.sink.error).toHaveBeenCalledWith(
      'Unhandled rejection: {"code":7}',
    );
  });

  describe("feedback loop", () => {
    it("does not forward a console.error made by the sink while forwarding", () => {
      const ref: { c?: { error: (...a: unknown[]) => void } } = {};
      const s = make({
        error: vi.fn(() => {
          ref.c!.error("sink noise");
        }),
      });
      ref.c = s.console;
      s.console.error("outer");
      expect(s.sink.error).toHaveBeenCalledTimes(1);
      expect(s.sink.error).toHaveBeenCalledWith("outer");
    });

    it("lets the sink's inner call reach the original console", () => {
      const ref: { c?: { warn: (...a: unknown[]) => void } } = {};
      const s = make({
        warn: vi.fn(() => {
          ref.c!.warn("inner");
        }),
      });
      ref.c = s.console;
      s.console.warn("outer");
      expect(s.originalWarn).toHaveBeenCalledTimes(2);
      expect(s.originalWarn).toHaveBeenLastCalledWith("inner");
    });

    it("does not forward a console.warn made by the error sink", () => {
      const ref: { c?: { warn: (...a: unknown[]) => void } } = {};
      const s = make({
        error: vi.fn(() => {
          ref.c!.warn("cross");
        }),
      });
      ref.c = s.console;
      s.console.error("outer");
      expect(s.sink.warn).not.toHaveBeenCalled();
    });

    it("forwards again after a sink call has finished", () => {
      const ref: { c?: { error: (...a: unknown[]) => void } } = {};
      const s = make({
        error: vi.fn(() => {
          ref.c!.error("inner");
        }),
      });
      ref.c = s.console;
      s.console.error("one");
      s.console.error("two");
      expect(s.sink.error).toHaveBeenCalledTimes(2);
    });
  });

  describe("failing sink", () => {
    it("does not throw when the sink throws synchronously", () => {
      const s = make({
        error: vi.fn(() => {
          throw new Error("sink down");
        }),
      });
      expect(() => s.console.error("x")).not.toThrow();
      expect(s.originalError).toHaveBeenCalledWith("x");
    });

    it("does not let a throwing sink escape an event listener", async () => {
      // EventTarget re-raises listener exceptions as uncaughtException.
      const seen: unknown[] = [];
      const listener = (e: unknown) => seen.push(e);
      process.on("uncaughtException", listener);
      try {
        const s = make({
          error: () => {
            throw new Error("sink down");
          },
        });
        s.events.dispatchEvent(rejectionEvent("r"));
        s.events.dispatchEvent(errorEvent({ message: "m" }));
        await new Promise((r) => setTimeout(r, 50));
        expect(seen).toEqual([]);
      } finally {
        process.off("uncaughtException", listener);
      }
    });

    it("handles a rejected promise so no unhandledrejection reaches process", async () => {
      // Plain functions: vi.fn attaches its own handler to returned promises.
      const seen: unknown[] = [];
      const listener = (r: unknown) => seen.push(r);
      process.on("unhandledRejection", listener);
      try {
        let calls = 0;
        const s = make({
          error: () => {
            calls++;
            return Promise.reject(new Error("async down"));
          },
          warn: () => {
            calls++;
            return Promise.reject(new Error("async down"));
          },
        });
        s.console.error("a");
        s.console.warn("b");
        await new Promise((r) => setTimeout(r, 50));
        expect(calls).toBe(2);
        expect(seen).toEqual([]);
      } finally {
        process.off("unhandledRejection", listener);
      }
    });
  });

  describe("budget", () => {
    it("forwards exactly LOG_BUDGET.messages within a window", () => {
      const s = make();
      for (let i = 0; i < LOG_BUDGET.messages + 20; i++) s.console.error(`m${i}`);
      expect(s.sink.error).toHaveBeenCalledTimes(LOG_BUDGET.messages);
    });

    it("still sends over-budget messages to the original console", () => {
      const s = make();
      const n = LOG_BUDGET.messages + 20;
      for (let i = 0; i < n; i++) s.console.error(`m${i}`);
      expect(s.originalError).toHaveBeenCalledTimes(n);
    });

    it("shares one budget between warn, error and events", () => {
      const s = make();
      for (let i = 0; i < 50; i++) s.console.warn("w");
      for (let i = 0; i < 30; i++) s.console.error("e");
      for (let i = 0; i < 20; i++) s.events.dispatchEvent(rejectionEvent("r"));
      s.console.error("over");
      expect(s.sink.error).toHaveBeenCalledTimes(50);
      expect(s.sink.warn).toHaveBeenCalledTimes(50);
    });

    it("keeps dropping until the window has expired", () => {
      const s = make();
      for (let i = 0; i < LOG_BUDGET.messages; i++) s.console.error("m");
      s.clock.set(1_000 + LOG_BUDGET.perMs - 1);
      s.console.error("late");
      expect(s.sink.error).toHaveBeenCalledTimes(LOG_BUDGET.messages);
    });

    it("opens a new window on the first message after expiry", () => {
      const s = make();
      for (let i = 0; i < LOG_BUDGET.messages; i++) s.console.error("m");
      s.clock.set(1_000 + LOG_BUDGET.perMs + 1);
      s.console.error("fresh");
      expect(s.sink.error).toHaveBeenCalledTimes(LOG_BUDGET.messages + 1);
      expect(s.sink.error).toHaveBeenLastCalledWith("fresh");
    });

    it("opens the window at the first forwarded message, not at install", () => {
      const s = make();
      s.clock.set(500_000);
      for (let i = 0; i < LOG_BUDGET.messages; i++) s.console.error("m");
      s.clock.set(500_000 + LOG_BUDGET.perMs - 1);
      s.console.error("still same window");
      expect(s.sink.error).toHaveBeenCalledTimes(LOG_BUDGET.messages);
    });

    it("reports the dropped count before the first message of the next window", () => {
      const order: string[] = [];
      const s = make({
        warn: vi.fn((m: string) => {
          order.push(`warn:${m}`);
        }),
        error: vi.fn((m: string) => {
          order.push(`error:${m}`);
        }),
      });
      for (let i = 0; i < LOG_BUDGET.messages + 7; i++) s.console.error("m");
      order.length = 0;
      s.clock.set(1_000 + LOG_BUDGET.perMs + 1);
      s.console.error("fresh");
      expect(order).toEqual([
        "warn:7 webview log message(s) suppressed",
        "error:fresh",
      ]);
    });

    it("sends no notice when nothing was dropped", () => {
      const s = make();
      s.console.error("a");
      s.clock.set(1_000 + LOG_BUDGET.perMs + 1);
      s.console.error("b");
      expect(s.sink.warn).not.toHaveBeenCalled();
    });

    it("does not repeat the notice in later windows", () => {
      const s = make();
      for (let i = 0; i < LOG_BUDGET.messages + 3; i++) s.console.error("m");
      s.clock.set(1_000 + LOG_BUDGET.perMs + 1);
      s.console.error("w2");
      s.clock.set(1_000 + 3 * LOG_BUDGET.perMs);
      s.console.error("w3");
      expect(s.sink.warn).toHaveBeenCalledTimes(1);
    });

    it("does not charge the notice against the new window's budget", () => {
      const s = make();
      for (let i = 0; i < LOG_BUDGET.messages + 3; i++) s.console.error("m");
      s.clock.set(1_000 + LOG_BUDGET.perMs + 1);
      for (let i = 0; i < LOG_BUDGET.messages; i++) s.console.error("n");
      s.console.error("over");
      expect(s.sink.error).toHaveBeenCalledTimes(2 * LOG_BUDGET.messages);
    });

    it("does not count the notice's own console traffic as feedback", () => {
      const s = make();
      for (let i = 0; i < LOG_BUDGET.messages + 2; i++) s.console.warn("m");
      s.clock.set(1_000 + LOG_BUDGET.perMs + 1);
      s.console.warn("fresh");
      expect(s.originalWarn).toHaveBeenCalledTimes(LOG_BUDGET.messages + 3);
    });
  });

  describe("uninstall", () => {
    it("restores the identical original console functions", () => {
      const s = make();
      expect(s.console.warn).not.toBe(s.originalWarn);
      s.uninstall();
      expect(s.console.warn).toBe(s.originalWarn);
      expect(s.console.error).toBe(s.originalError);
    });

    it("removes both event listeners", () => {
      const s = make();
      s.uninstall();
      s.events.dispatchEvent(errorEvent({ message: "m" }));
      s.events.dispatchEvent(rejectionEvent("r"));
      expect(s.sink.error).not.toHaveBeenCalled();
    });

    it("sends nothing to the sink after uninstall", () => {
      const s = make();
      s.uninstall();
      s.console.warn("w");
      s.console.error("e");
      expect(s.sink.warn).not.toHaveBeenCalled();
      expect(s.sink.error).not.toHaveBeenCalled();
    });
  });
});
