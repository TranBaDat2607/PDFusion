import { useState } from "react";
import { AlertTriangle, FileText, FolderOpen, Loader2, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";

interface StartupScreenProps {
  state: { status: "starting" } | { status: "error"; message: string };
}

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

  const invokeCommand = async (command: string) => {
    setBusy(true);
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke(command);
    } catch {
      // Nothing useful to show: if the shell can't be reached, this screen is
      // already telling the user the app didn't come up.
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full w-full items-center justify-center bg-background p-6">
      <div className="flex max-w-2xl flex-col items-center gap-4 text-center">
        <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10">
          <FileText className="h-7 w-7 text-primary" />
        </div>
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">PDFusion</h1>
          {state.status === "starting" && (
            <p className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Starting PDFusion…
            </p>
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
              {import.meta.env.DEV && (
                <p className="text-xs text-muted-foreground">
                  Dev: if the error mentions Python, set{" "}
                  <code className="font-mono">PDFUSION_PYTHON</code> to your
                  conda env's <code className="font-mono">python.exe</code> and
                  restart.
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
