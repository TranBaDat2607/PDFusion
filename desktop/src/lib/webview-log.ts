/**
 * Copies the webview's warnings and errors into the shell's `shell.log`.
 *
 * Without it the webview has no log file at all: its errors reach only the
 * devtools console, which nobody has open when something goes wrong. That is
 * how a viewer crash once blanked the whole window while `app.log` and
 * `shell.log` both read as a clean run.
 *
 * The sink is the Tauri log plugin, whose calls are async IPC that can fail,
 * and which this module must never turn into a crash or a loop of its own.
 */

export interface LogSink {
  warn(message: string): Promise<void> | void;
  error(message: string): Promise<void> | void;
}

export interface WebviewLogTarget {
  console: Pick<Console, "warn" | "error">;
  /** Receives `error` and `unhandledrejection` events: `window` in the app. */
  events: EventTarget;
  now?: () => number;
}

/** A stack or a serialized object can be enormous; one line of `shell.log`
 *  should stay readable, and the file is capped at 5 MB before it rotates. */
export const MAX_MESSAGE_CHARS = 8000;
/** A warning inside a render loop fires every frame. Without a cap it would
 *  rotate `shell.log` through its five backups in minutes and push out the
 *  shell's own lines, which are the ones worth keeping. */
export const LOG_BUDGET = { messages: 100, perMs: 60_000 };

type Level = "warn" | "error";

function formatPart(value: unknown): string {
  if (typeof value === "string") return value;
  if (value instanceof Error) {
    return value.stack || `${value.name}: ${value.message}`;
  }
  try {
    const json = JSON.stringify(value);
    if (json !== undefined) return json;
  } catch {
    // Circular, or a BigInt: fall through to the plain form.
  }
  return String(value);
}

export function formatLogMessage(args: readonly unknown[]): string {
  const text = args.map(formatPart).join(" ");
  return text.length > MAX_MESSAGE_CHARS
    ? `${text.slice(0, MAX_MESSAGE_CHARS - 1)}…`
    : text;
}

export function installWebviewLogging(
  target: WebviewLogTarget,
  sink: LogSink,
): () => void {
  const now = target.now ?? Date.now;
  const original = {
    warn: target.console.warn,
    error: target.console.error,
  };
  let installed = true;
  // Set while the sink runs. The plugin, or anything it calls, may itself log
  // to the console; forwarding that would recurse without end.
  let forwarding = false;
  let windowStart: number | null = null;
  let sent = 0;
  let dropped = 0;

  const send = (level: Level, message: string) => {
    forwarding = true;
    try {
      const result = sink[level](message);
      // Unhandled, a failed IPC call would be an `unhandledrejection`, which
      // this module forwards: a loop through the sink that just failed.
      if (result && typeof result.then === "function") {
        result.then(undefined, () => undefined);
      }
    } catch {
      // Logging must never be the thing that breaks the app.
    } finally {
      forwarding = false;
    }
  };

  const forward = (level: Level, message: () => string) => {
    if (!installed || forwarding) return;
    const at = now();
    if (windowStart === null || at - windowStart >= LOG_BUDGET.perMs) {
      windowStart = at;
      sent = 0;
      if (dropped > 0) {
        send("warn", `${dropped} webview log message(s) suppressed`);
        dropped = 0;
      }
    }
    if (sent >= LOG_BUDGET.messages) {
      dropped++;
      return;
    }
    sent++;
    send(level, message());
  };

  const wrap =
    (level: Level) =>
    (...args: unknown[]) => {
      original[level].apply(target.console, args);
      forward(level, () => formatLogMessage(args));
    };

  const onError = (event: Event) => {
    const { error, message } = event as ErrorEvent;
    forward("error", () => formatLogMessage([error ?? message]));
  };
  const onRejection = (event: Event) => {
    const { reason } = event as PromiseRejectionEvent;
    forward("error", () => `Unhandled rejection: ${formatLogMessage([reason])}`);
  };

  target.console.warn = wrap("warn");
  target.console.error = wrap("error");
  target.events.addEventListener("error", onError);
  target.events.addEventListener("unhandledrejection", onRejection);

  return () => {
    installed = false;
    target.console.warn = original.warn;
    target.console.error = original.error;
    target.events.removeEventListener("error", onError);
    target.events.removeEventListener("unhandledrejection", onRejection);
  };
}
