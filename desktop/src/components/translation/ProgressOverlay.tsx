import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Clock, Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { TranslatedFileActions } from "@/components/translation/TranslatedFileActions";
import { isTranslationBusy, type TranslationState } from "@/hooks/useTranslation";
import { useAppStore } from "@/lib/store";
import { pagesReady, pagesRemaining } from "@/lib/translation-progress";

interface ProgressOverlayProps {
  state: TranslationState;
  onCancel: () => void;
  onDismiss: () => void;
}

function formatEta(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 1) return "<1s";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds - m * 60);
  return s === 0 ? `${m}m` : `${m}m ${s}s`;
}

/**
 * Server-side ETA only updates per chunk_ready. To avoid a stale, jumpy number,
 * we anchor on the server value and decay it locally with a 1 Hz ticker. When
 * a new chunk_ready arrives, the anchor refreshes — so the displayed ETA stays
 * within ~1s of the server's view but ticks smoothly between updates.
 */
function useSmoothEta(
  anchor: number | null | undefined,
  anchorAt: number | null | undefined,
  running: boolean,
): number | null {
  const [, setTick] = useState(0);
  const hasAnchor = anchor != null && anchorAt != null;

  useEffect(() => {
    if (!running || !hasAnchor) return;
    const id = window.setInterval(() => setTick((t) => t + 1), 1000);
    return () => window.clearInterval(id);
  }, [running, hasAnchor]);

  if (!hasAnchor) return null;
  const sinceAnchor = (Date.now() - (anchorAt as number)) / 1000;
  return Math.max(0, (anchor as number) - sinceAnchor);
}

export function ProgressOverlay({
  state,
  onCancel,
  onDismiss,
}: ProgressOverlayProps) {
  const chunkProgress = useAppStore((s) => s.chunkProgress);
  const exportedPath = useAppStore((s) => s.exportedPdfPath);
  const smoothEta = useSmoothEta(
    state.etaSeconds,
    state.etaAnchorAt,
    state.status === "running",
  );
  if (state.status === "idle") return null;

  const busy = isTranslationBusy(state);
  // Pages, not chunks: Argos translates 3 pages per chunk, so `totalChunks`
  // is not a page count.
  const donePages = chunkProgress ? pagesReady(chunkProgress) : 0;
  const totalPages = chunkProgress?.totalPages ?? null;
  const pagesLeft = chunkProgress ? pagesRemaining(chunkProgress) : null;

  // Only `exportedPath` is a copy the user keeps. The translated path is a
  // rolling file in a per-job %TEMP% dir that the next translation, app exit,
  // and the sidecar's orphan sweep all delete — labelling it "Saved to" sends
  // the user looking for a file that is gone.
  const resultPath = exportedPath ?? state.translatedPath;
  const resultLabel = exportedPath
    ? "Saved to: "
    : state.status === "cancelled"
      ? "Partial preview at: "
      : "Preview at: ";

  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-4 z-30 flex justify-center">
      <div className="pointer-events-auto w-full max-w-xl rounded-xl border border-border bg-background/95 p-4 shadow-xl backdrop-blur">
        <div className="mb-2 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            {busy && (
              <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" />
            )}
            <span className="truncate text-sm font-medium">
              {state.status === "done" && "Translation complete"}
              {state.status === "error" && "Translation failed"}
              {state.status === "cancelled" && "Translation cancelled"}
              {state.status === "cancelling" && "Cancelling…"}
              {state.status === "running" && (state.stage || "Translating…")}
            </span>
          </div>
          {busy ? (
            <Button
              size="sm"
              variant="ghost"
              onClick={onCancel}
              // Already draining — a second click has nothing left to cancel.
              disabled={state.status === "cancelling"}
            >
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
            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
              {chunkProgress && (
                <span className="font-medium text-primary">
                  {totalPages != null
                    ? `${donePages} of ${totalPages} page${totalPages === 1 ? "" : "s"} translated`
                    : `${donePages} page${donePages === 1 ? "" : "s"} translated`}
                </span>
              )}
              {smoothEta != null && smoothEta > 0 && (
                <span className="flex items-center gap-1 text-muted-foreground">
                  <Clock className="h-3 w-3" />~{formatEta(smoothEta)} remaining
                  {pagesLeft != null && pagesLeft > 0 && (
                    <span className="opacity-70">
                      &middot; {pagesLeft} page{pagesLeft === 1 ? "" : "s"} left
                    </span>
                  )}
                </span>
              )}
            </div>
            {state.message && (
              <p className="mt-2 line-clamp-2 text-xs text-muted-foreground">
                {state.message}
              </p>
            )}
            <AnimatePresence mode="popLayout">
              {state.lastParagraph && (
                <motion.div
                  key={state.lastParagraph.seq}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -6 }}
                  transition={{ duration: 0.18 }}
                  className="mt-2 overflow-hidden rounded-md border border-border/50 bg-muted/40 px-2.5 py-1.5 text-[10px] leading-snug"
                >
                  <div className="mb-0.5 flex items-center gap-2 text-muted-foreground">
                    <span className="font-mono">
                      ¶ {state.lastParagraph.index}
                    </span>
                    <span className="rounded bg-background px-1 py-0.5 font-mono uppercase">
                      {state.lastParagraph.service}
                    </span>
                  </div>
                  <div className="line-clamp-1 text-muted-foreground/80">
                    {state.lastParagraph.source}
                  </div>
                  <div className="line-clamp-1 font-medium text-foreground">
                    → {state.lastParagraph.target}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </>
        )}
        {state.status === "error" && state.error && (
          <p className="text-xs text-destructive">{state.error}</p>
        )}
        {(state.status === "done" || state.status === "cancelled") &&
          resultPath && (
            <div className="space-y-2.5">
              <p className="text-xs text-muted-foreground">
                {resultLabel}
                <span className="break-all font-mono text-foreground">
                  {resultPath}
                </span>
                {!exportedPath && (
                  <span className="mt-0.5 block text-[11px] text-amber-600 dark:text-amber-500">
                    Temporary file — save it to keep this translation.
                  </span>
                )}
              </p>
              <TranslatedFileActions />
            </div>
          )}
      </div>
    </div>
  );
}
