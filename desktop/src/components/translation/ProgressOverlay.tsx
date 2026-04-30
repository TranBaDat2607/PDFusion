import { Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import type { TranslationState } from "@/hooks/useTranslation";

interface ProgressOverlayProps {
  state: TranslationState;
  onCancel: () => void;
  onDismiss: () => void;
}

export function ProgressOverlay({
  state,
  onCancel,
  onDismiss,
}: ProgressOverlayProps) {
  if (state.status === "idle") return null;

  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-4 z-30 flex justify-center">
      <div className="pointer-events-auto w-full max-w-xl rounded-xl border border-border bg-background/95 p-4 shadow-xl backdrop-blur">
        <div className="mb-2 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            {state.status === "running" && (
              <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" />
            )}
            <span className="truncate text-sm font-medium">
              {state.status === "done" && "Translation complete"}
              {state.status === "error" && "Translation failed"}
              {state.status === "cancelled" && "Translation cancelled"}
              {state.status === "running" && (state.stage || "Translating…")}
            </span>
          </div>
          {state.status === "running" ? (
            <Button size="sm" variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
          ) : (
            <Button
              size="icon"
              variant="ghost"
              onClick={onDismiss}
              aria-label="Dismiss"
              className="h-7 w-7"
            >
              <X className="h-4 w-4" />
            </Button>
          )}
        </div>
        {state.status === "running" && (
          <>
            <Progress value={state.progress} className="h-1.5" />
            {state.message && (
              <p className="mt-2 line-clamp-2 text-xs text-muted-foreground">
                {state.message}
              </p>
            )}
          </>
        )}
        {state.status === "error" && state.error && (
          <p className="text-xs text-destructive">{state.error}</p>
        )}
        {state.status === "done" && state.translatedPath && (
          <p className="text-xs text-muted-foreground">
            Saved to:{" "}
            <span className="font-mono text-foreground">
              {state.translatedPath}
            </span>
          </p>
        )}
      </div>
    </div>
  );
}
