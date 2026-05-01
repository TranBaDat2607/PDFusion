import { AlertTriangle, FileText, Loader2 } from "lucide-react";

interface StartupScreenProps {
  state: { status: "starting" } | { status: "error"; message: string };
}

export function StartupScreen({ state }: StartupScreenProps) {
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
              Starting Python sidecar…
            </p>
          )}
          {state.status === "error" && (
            <div className="flex flex-col items-center gap-2">
              <p className="flex items-center gap-2 text-sm text-destructive">
                <AlertTriangle className="h-4 w-4" />
                Sidecar failed to start
              </p>
              <pre className="w-full whitespace-pre-wrap break-words rounded-md border border-destructive/30 bg-destructive/5 p-3 text-left text-xs text-destructive">
                {state.message}
              </pre>
              <p className="text-xs text-muted-foreground">
                If the error mentions Python: set{" "}
                <code className="font-mono">PDFUSION_PYTHON</code> to your conda
                env's <code className="font-mono">python.exe</code> and restart
                the app.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
