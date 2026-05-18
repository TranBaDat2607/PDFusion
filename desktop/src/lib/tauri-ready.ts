// `__TAURI_INTERNALS__` is the IPC bridge Tauri 2 injects before user JS runs.
// In dev the Vite bundle can occasionally race that injection, so we poll briefly.

declare global {
  interface Window {
    __TAURI_INTERNALS__?: unknown;
  }
}

const HINTS = {
  browserTab:
    "→ This looks like a regular browser tab. Close it and use the Tauri desktop window opened by `pnpm tauri dev`.",
  noInjection:
    "→ Page is in some webview but Tauri injection didn't run. Check tauri.conf.json `app.security.csp` and `app.withGlobalTauri`.",
} as const;

export function tauriAvailable(): boolean {
  return typeof window !== "undefined" && !!window.__TAURI_INTERNALS__;
}

export function diagnostics(): string {
  if (typeof window === "undefined") return "no window object";
  const url = window.location.href;
  const ua = navigator.userAgent;
  const inTauriUA =
    ua.includes("Tauri") || ua.includes("WebView2") || ua.includes("wry");
  const looksLikeBrowser =
    !inTauriUA && /^https?:\/\/(localhost|127\.0\.0\.1):/.test(url);
  return [
    `url: ${url}`,
    `userAgent: ${ua}`,
    `__TAURI_INTERNALS__: ${typeof window.__TAURI_INTERNALS__}`,
    looksLikeBrowser ? HINTS.browserTab : HINTS.noInjection,
  ].join("\n");
}

export function waitForTauri(timeoutMs = 8000): Promise<void> {
  if (tauriAvailable()) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const start = performance.now();
    const tick = () => {
      if (tauriAvailable()) return resolve();
      if (performance.now() - start > timeoutMs) {
        reject(
          new Error(
            "Tauri IPC bridge (`__TAURI_INTERNALS__`) never appeared.\n\n" +
              diagnostics(),
          ),
        );
        return;
      }
      setTimeout(tick, 50);
    };
    tick();
  });
}
