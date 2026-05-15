import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { api } from "@/lib/api-client";
import { streamEvents } from "@/lib/sse";
import { useAppStore } from "@/lib/store";

interface ProgressUpdate {
  stage?: string;
  progress_percent?: number;
  message?: string;
  current_step?: number;
  total_steps?: number;
}

interface CompletionPayload {
  success?: boolean;
  translated_file?: string | null;
  original_file?: string | null;
  processing_time_seconds?: number | null;
  pages_processed?: number | null;
}

interface ChunkReadyPayload {
  chunk_index: number;
  total_chunks: number;
  pages_in_chunk: [number, number];
  rolling_pdf_path: string;
  progress_percent: number;
  elapsed_seconds?: number | null;
  eta_seconds?: number | null;
  pages_per_second?: number | null;
}

interface ParagraphTranslatedPayload {
  source_preview: string;
  target_preview: string;
  paragraphs_seen: number;
  service: string;
}

export interface TranslationState {
  status: "idle" | "running" | "done" | "error" | "cancelled";
  stage: string;
  progress: number; // 0-100
  message: string;
  error?: string;
  translatedPath?: string | null;
  /** Server-reported ETA at the moment of the last chunk_ready, in seconds.
   *  Frontend smooth-decays this between updates. */
  etaSeconds?: number | null;
  /** When the etaSeconds anchor was received (epoch ms). */
  etaAnchorAt?: number | null;
  /** Cumulative elapsed seconds at last server update — useful for sanity-clamping. */
  elapsedSeconds?: number | null;
  /** Most-recent paragraph translation — drives the live preview ticker. */
  lastParagraph?: {
    source: string;
    target: string;
    index: number;
    service: string;
    /** Monotonic key to force re-mount animation on each new tick. */
    seq: number;
  } | null;
}

const INITIAL: TranslationState = {
  status: "idle",
  stage: "",
  progress: 0,
  message: "",
};

export function useTranslation() {
  const [state, setState] = useState<TranslationState>(INITIAL);
  const abortRef = useRef<AbortController | null>(null);
  const setTranslatedPdfPath = useAppStore((s) => s.setTranslatedPdfPath);
  const setActiveJob = useAppStore((s) => s.setActiveTranslationJob);
  const setChunkProgress = useAppStore((s) => s.setChunkProgress);

  const start = useCallback(
    async (filePath: string) => {
      setState({ ...INITIAL, status: "running", stage: "Starting…" });
      setChunkProgress(null);

      // Seed priority from the page the user is currently looking at — defaults
      // to page 1 if no PDF is loaded yet (shouldn't happen in normal flow).
      const visiblePage = useAppStore.getState().visiblePage ?? 1;

      let jobId: string;
      try {
        const accepted = await api.post<{ job_id: string }>("/translate", {
          file_path: filePath,
          visible_page: visiblePage,
        });
        jobId = accepted.job_id;
        setActiveJob(jobId);
      } catch (e) {
        setState({
          ...INITIAL,
          status: "error",
          error: (e as Error).message,
        });
        return;
      }

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        await streamEvents<
          | ProgressUpdate
          | CompletionPayload
          | ChunkReadyPayload
          | ParagraphTranslatedPayload
          | { message: string }
        >({
          path: `/translate/${jobId}/events`,
          signal: controller.signal,
          onEvent: ({ type, data }) => {
            if (type === "progress") {
              const p = data as ProgressUpdate;
              // Sidecar emits stage="fallback" once when the requested LLM has
              // no key and we silently switch to Argos. Surface that to the user
              // but don't pollute the progress bar with the fallback stage label.
              if (p.stage === "fallback" && p.message) {
                toast.info(p.message);
              } else {
                setState((s) => ({
                  ...s,
                  stage: p.stage ?? s.stage,
                  progress: p.progress_percent ?? s.progress,
                  message: p.message ?? s.message,
                }));
              }
            } else if (type === "paragraph_translated") {
              const p = data as ParagraphTranslatedPayload;
              setState((s) => ({
                ...s,
                lastParagraph: {
                  source: p.source_preview,
                  target: p.target_preview,
                  index: p.paragraphs_seen,
                  service: p.service,
                  seq: (s.lastParagraph?.seq ?? 0) + 1,
                },
              }));
            } else if (type === "chunk_ready") {
              const c = data as ChunkReadyPayload;
              setTranslatedPdfPath(c.rolling_pdf_path);
              setChunkProgress({
                chunksReady: c.chunk_index + 1,
                totalChunks: c.total_chunks,
                pagesReady: c.pages_in_chunk[1],
              });
              setState((s) => ({
                ...s,
                progress: c.progress_percent,
                stage: `Chunk ${c.chunk_index + 1}/${c.total_chunks} ready`,
                message: `Pages 1–${c.pages_in_chunk[1]} translated`,
                etaSeconds: c.eta_seconds ?? null,
                etaAnchorAt:
                  c.eta_seconds != null ? Date.now() : (s.etaAnchorAt ?? null),
                elapsedSeconds: c.elapsed_seconds ?? null,
              }));
            } else if (type === "done") {
              const c = data as CompletionPayload;
              if (c.translated_file) {
                setTranslatedPdfPath(c.translated_file);
              }
              setState((s) => ({
                ...s,
                status: "done",
                progress: 100,
                stage: "Done",
                translatedPath: c.translated_file ?? null,
              }));
            } else if (type === "cancelled") {
              setState((s) => ({ ...s, status: "cancelled" }));
            } else if (type === "error") {
              const e = data as { message: string };
              setState((s) => ({
                ...s,
                status: "error",
                error: e.message,
              }));
            }
          },
        });
      } catch (e) {
        setState((s) => ({
          ...s,
          status: "error",
          error: (e as Error).message,
        }));
      } finally {
        setActiveJob(null);
        abortRef.current = null;
      }
    },
    [setActiveJob, setTranslatedPdfPath, setChunkProgress],
  );

  const cancel = useCallback(async () => {
    const jobId = useAppStore.getState().activeTranslationJob;
    if (!jobId) return;
    try {
      await api.post(`/translate/${jobId}/cancel`);
    } catch {
      // Best-effort
    }
    abortRef.current?.abort();
  }, []);

  const reset = useCallback(() => setState(INITIAL), []);

  // Live re-prioritization: when the user scrolls during translation, POST
  // the new visible page so backend workers pivot to translating pages near
  // the current view next. Debounced to avoid flooding while scrolling.
  const activeJob = useAppStore((s) => s.activeTranslationJob);
  const visiblePage = useAppStore((s) => s.visiblePage);
  const lastSentPageRef = useRef<number | null>(null);
  useEffect(() => {
    if (state.status !== "running" || !activeJob || visiblePage == null) return;
    if (lastSentPageRef.current === visiblePage) return;
    const timer = window.setTimeout(() => {
      lastSentPageRef.current = visiblePage;
      void api
        .post(`/translate/${activeJob}/reprioritize?visible_page=${visiblePage}`)
        .catch(() => undefined);
    }, 400);
    return () => window.clearTimeout(timer);
  }, [state.status, activeJob, visiblePage]);

  return { state, start, cancel, reset };
}
