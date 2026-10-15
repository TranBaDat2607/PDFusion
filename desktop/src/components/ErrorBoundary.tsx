import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * The last line of defence for an error thrown while rendering or inside an
 * effect. Without it React unmounts the whole tree and leaves a blank white
 * window with no hint of what happened, which is how a viewer bug once looked
 * like the app had died: the only trace was in the webview's console.
 *
 * Reload re-runs the webview, not the shell, so the sidecar survives it, and
 * so does a job in flight: it keeps translating (and spending provider calls)
 * with nothing left to cancel it, because job ids live only in this window's
 * memory. The sweep in `jobs.py` skips a job whose worker is still running.
 * The copy says so, so the user doesn't start the same run twice unawares.
 */
export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("[PDFusion] UI crashed:", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="flex h-screen w-full items-center justify-center bg-background p-6">
        <div className="flex max-w-2xl flex-col items-center gap-4 text-center">
          <AlertTriangle className="h-8 w-8 text-destructive" />
          <div className="space-y-1">
            <h1 className="text-base font-semibold">Something went wrong</h1>
            <p className="text-sm text-muted-foreground">
              The window hit an error it couldn't recover from. Reload to
              bring it back. A translation that was running carries on in the
              background, but this window won't show its progress or result.
            </p>
          </div>
          <pre className="max-h-48 w-full overflow-auto rounded-md bg-muted p-3 text-left font-mono text-xs">
            {error.stack ?? String(error)}
          </pre>
          <Button size="sm" onClick={() => window.location.reload()}>
            <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
            Reload
          </Button>
        </div>
      </div>
    );
  }
}
