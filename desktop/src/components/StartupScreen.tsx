import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { AlertTriangle, FileText, FolderOpen, Loader2, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";

interface StartupScreenProps {
  state:
    | { status: "starting" }
    | { status: "error"; message: string }
    | { status: "crashed"; code: number | null };
}

/**
 * How long "Starting PDFusion…" runs before the screen offers a way out.
 *
 * The shell waits 90 s for the sidecar's READY line and then another 30 s for
 * `/auth/ping` (`READY_TIMEOUT` / `HEALTH_TIMEOUT` in `sidecar.rs`), so a
 * sidecar that never comes up parks the user here for two minutes before the
 * error branch — the only one with Retry and the logs folder — is reachable at
 * all. Those deadlines are sized for a PyInstaller cold start behind Defender,
 * not for Python work: a healthy boot prints READY in about a second. Anything
 * still spinning at this mark is already abnormal, and waiting out the full
 * timeout with nothing but a spinner is not something to make the user do.
 */
const SLOW_START_MS = 15_000;

/**
 * What the user sees before the app is usable.
 *
 * The copy is deliberately in the app's own terms. "Starting Python sidecar…"
 * and "set PDFUSION_PYTHON to your conda env's python.exe" describe this
 * repository's dev setup, not anything an end user installed from the .msi can
 * act on — so the interpreter hint is kept behind `import.meta.env.DEV`, which
 * Vite compiles to `false` (and tree-shakes) in a production build.
 *
 * Retry restarts the whole app rather than re-spawning the sidecar: the handle
 * is a `OnceCell` set once per process, so re-entering that lifecycle would
 * mean two spawn paths and a window where two Python processes share one
 * `chroma_db`. See `restart_app` in `lib.rs`.
 */
export function StartupScreen({ state }: StartupScreenProps) {
  const [busy, setBusy] = useState(false);
  const [slow, setSlow] = useState(false);

  const starting = state.status === "starting";

  useEffect(() => {
    if (!starting) return;
    const timer = setTimeout(() => setSlow(true), SLOW_START_MS);
    return () => clearTimeout(timer);
  }, [starting]);

  const invokeCommand = async (command: string) => {
    setBusy(true);
    try {
      await invoke(command);
    } catch {
      // Nothing useful to show: if the shell can't be reached, this screen is
      // already telling the user the app didn't come up.
    } finally {
      setBusy(false);
    }
  };

  // Shared by both branches: a startup that is merely slow needs the same two
  // escapes as one that has already failed.
  const actions = (
    <div className="flex flex-wrap items-center justify-center gap-2">
      <Button
        size="sm"
        onClick={() => void invokeCommand("restart_app")}
        disabled={busy}
      >
        <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
        Retry
      </Button>
      <Button
        size="sm"
        variant="outline"
        onClick={() => void invokeCommand("open_logs_folder")}
        disabled={busy}
      >
        <FolderOpen className="mr-1.5 h-3.5 w-3.5" />
        Show logs folder
      </Button>
    </div>
  );

  const devHint = import.meta.env.DEV && (
    <p className="text-xs text-muted-foreground">
      Dev: if the sidecar can't find an interpreter, set{" "}
      <code className="font-mono">PDFUSION_PYTHON</code> to your conda env's{" "}
      <code className="font-mono">python.exe</code> and restart.
    </p>
  );

  return (
    <div className="flex h-full w-full items-center justify-center bg-background p-6">
      <div className="flex max-w-2xl flex-col items-center gap-4 text-center">
        <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10">
          <FileText className="h-7 w-7 text-primary" />
        </div>
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">PDFusion</h1>
          {starting && (
            <div className="flex flex-col items-center gap-3">
              <p className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Starting PDFusion…
              </p>
              {slow && (
                <>
                  <p className="text-xs text-muted-foreground">
                    This is taking longer than usual. It may still finish — a
                    first launch after installing has to get past Windows'
                    on-access scanner. You can wait, or take a look at the logs.
                  </p>
                  {actions}
                  {devHint}
                </>
              )}
            </div>
          )}
          {state.status === "error" && (
            <div className="flex flex-col items-center gap-3">
              <p className="flex items-center gap-2 text-sm text-destructive">
                <AlertTriangle className="h-4 w-4" />
                PDFusion couldn't start
              </p>
              <pre className="w-full whitespace-pre-wrap break-words rounded-md border border-destructive/30 bg-destructive/5 p-3 text-left text-xs text-destructive">
                {state.message}
              </pre>
              {actions}
              {devHint}
            </div>
          )}
          {state.status === "crashed" && (
            <div className="flex flex-col items-center gap-3">
              <p className="flex items-center gap-2 text-sm text-destructive">
                <AlertTriangle className="h-4 w-4" />
                PDFusion's background process stopped unexpectedly
              </p>
              <p className="text-xs text-muted-foreground">
                It crashed again shortly after restarting, so automatic
                recovery has been paused.
                {state.code !== null && ` (exit code ${state.code})`}
              </p>
              {actions}
              {devHint}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
