import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { ApiError, api } from "@/lib/api-client";
import { streamEvents } from "@/lib/sse";
import { useAppStore } from "@/lib/store";
import { buildTranslateBody } from "@/lib/translate-request";

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
  cache_hit?: boolean;
  cached_at?: string | null;
  /** Language the run actually produced, straight from the processor. Names
   *  the default file in the Save dialog — see `translationTargetLang`. */
  target_lang?: string | null;
  /** Paragraphs handed back as source text because the translator errored.
   *  BabelDOC swallows those errors, so this count is the only evidence that
   *  a "complete" translation is partly the original document. */
  failed_paragraphs?: number;
  total_paragraphs?: number;
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
  cache_hit?: boolean;
  cached_at?: string | null;
}

interface ParagraphTranslatedPayload {
  source_preview: string;
  target_preview: string;
  paragraphs_seen: number;
  service: string;
}

export interface TranslationState {
  /** `cancelling` is the window between the user clicking Cancel and the
   *  backend's terminal event. The worker is still draining, and its cancel
   *  path runs `cleanup_partial_artifacts()`, which deletes all but the newest
   *  rolling PDF — so the artifact on disk is not settled yet and nothing may
   *  act on it. Treat it as busy, not as a result. */
  status: "idle" | "running" | "cancelling" | "done" | "error" | "cancelled";
  stage: string;
  progress: number; // 0-100
  message: string;
  error?: string;
  translatedPath?: string | null;
  /** Non-zero means the finished PDF is only partly translated — those
   *  paragraphs are still in the source language. Such a run is deliberately
   *  not cached, so Re-translate can produce a better one. */
  failedParagraphs?: number;
  totalParagraphs?: number;
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

/** Statuses where a backend worker may still be touching the artifact. */
export function isTranslationBusy(state: TranslationState): boolean {
  return state.status === "running" || state.status === "cancelling";
}

const RELATIVE_TIME = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "earlier";
  const diffSec = Math.round((then - Date.now()) / 1000);
  const abs = Math.abs(diffSec);
  if (abs < 60) return RELATIVE_TIME.format(diffSec, "second");
  if (abs < 3600) return RELATIVE_TIME.format(Math.round(diffSec / 60), "minute");
  if (abs < 86400) return RELATIVE_TIME.format(Math.round(diffSec / 3600), "hour");
  return RELATIVE_TIME.format(Math.round(diffSec / 86400), "day");
}

export interface StartOptions {
  /** Skip the PDF-level cache for this run — forces a fresh translation. */
  bypassCache?: boolean;
  /** The toolbar's current selection. Passed explicitly rather than left to
   *  the sidecar's config lookup: changing a dropdown fires an async PUT, so a
   *  Translate click landing before that PUT would otherwise run the *previous*
   *  selection. Omitted values fall back to the configured defaults. */
  sourceLang?: string | null;
  targetLang?: string | null;
  service?: string | null;
}

export function useTranslation() {
  const [state, setState] = useState<TranslationState>(INITIAL);
  const abortRef = useRef<AbortController | null>(null);
  const adoptArtifact = useAppStore((s) => s.adoptTranslatedArtifact);
  const setTranslationTargetLang = useAppStore(
    (s) => s.setTranslationTargetLang,
  );
  const setActiveJob = useAppStore((s) => s.setActiveTranslationJob);
  const setChunkProgress = useAppStore((s) => s.setChunkProgress);
  const bumpTranslatedReloadKey = useAppStore(
    (s) => s.bumpTranslatedReloadKey,
  );

  const start = useCallback(
    async (filePath: string, opts: StartOptions = {}) => {
      setState({ ...INITIAL, status: "running", stage: "Starting…" });
      setChunkProgress(null);
      // NOTE: `exportedPdfPath` is deliberately NOT cleared here. Starting a
      // job immediately rmtree's the previous job's temp dir, so if this run
      // fails before its first artifact, clearing now would throw away the
      // pointer to the user's saved copy — the only one still on disk — while
      // leaving `translatedPdfPath` aimed at a file that no longer exists.
      // It's cleared below instead, when a new artifact actually arrives.
      //
      // Bump the viewer's reload nonce only when a translated PDF is already
      // loaded — i.e. this is a Re-translate that will overwrite the file at
      // the same `_translated_v*.pdf` path and pdf.js needs a forced refetch.
      // First-time translations have nothing in the translated panel yet, so
      // bumping there only costs an unnecessary pdf.js fetch + parse of the
      // *source* PDF without changing anything visible.
      if (useAppStore.getState().translatedPdfPath != null) {
        bumpTranslatedReloadKey();
      }

      // Seed priority from the page the user is currently looking at — defaults
      // to page 1 if no PDF is loaded yet (shouldn't happen in normal flow).
      const visiblePage = useAppStore.getState().visiblePage ?? 1;
      // The backend may serve a cached translation as a single synthetic
      // chunk_ready + done. We want the "Loaded from cache" toast to fire
      // exactly once per run, hence this latch.
      let cacheToastFired = false;

      let jobId: string;
      try {
        const accepted = await api.post<{ job_id: string }>(
          "/translate",
          buildTranslateBody({
            filePath,
            visiblePage,
            bypassCache: opts.bypassCache,
            sourceLang: opts.sourceLang,
            targetLang: opts.targetLang,
            service: opts.service,
          }),
        );
        jobId = accepted.job_id;
        setActiveJob(jobId);
      } catch (e) {
        // The sidecar pre-flights the language pair and answers 422 with a
        // sentence meant for the user ("Argos translates English → Vietnamese
        // only…"). Show that, not `ApiError`'s "422: " prefix.
        const message = e instanceof ApiError ? e.detail : (e as Error).message;
        if (e instanceof ApiError && e.status === 422) {
          toast.error("Can't translate this combination", {
            description: message,
          });
        }
        setState({ ...INITIAL, status: "error", error: message });
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
              if (c.cache_hit && !cacheToastFired) {
                cacheToastFired = true;
                const when = c.cached_at ? formatRelative(c.cached_at) : "earlier";
                toast.success(`Loaded from cache · translated ${when}`);
              }
              adoptArtifact(c.rolling_pdf_path);
              setChunkProgress({
                chunksReady: c.chunk_index + 1,
                totalChunks: c.total_chunks,
                pagesReady: c.pages_in_chunk[1],
              });
              setState((s) => ({
                ...s,
                progress: c.progress_percent,
                stage: c.cache_hit
                  ? "Loaded from cache"
                  : `Chunk ${c.chunk_index + 1}/${c.total_chunks} ready`,
                message: c.cache_hit
                  ? `All ${c.pages_in_chunk[1]} page(s) ready`
                  : `Pages 1–${c.pages_in_chunk[1]} translated`,
                etaSeconds: c.eta_seconds ?? null,
                etaAnchorAt:
                  c.eta_seconds != null ? Date.now() : (s.etaAnchorAt ?? null),
                elapsedSeconds: c.elapsed_seconds ?? null,
              }));
            } else if (type === "done") {
              const c = data as CompletionPayload;
              if (c.cache_hit && !cacheToastFired) {
                cacheToastFired = true;
                const when = c.cached_at ? formatRelative(c.cached_at) : "earlier";
                toast.success(`Loaded from cache · translated ${when}`);
              }
              if (c.translated_file) {
                adoptArtifact(c.translated_file);
              }
              if (c.target_lang) setTranslationTargetLang(c.target_lang);
              setState((s) => ({
                ...s,
                status: "done",
                progress: 100,
                stage: c.cache_hit ? "Loaded from cache" : "Done",
                translatedPath: c.translated_file ?? null,
                failedParagraphs: c.failed_paragraphs ?? 0,
                totalParagraphs: c.total_paragraphs ?? 0,
              }));
            } else if (type === "cancelled") {
              const c = data as CompletionPayload;
              if (c.translated_file) {
                adoptArtifact(c.translated_file);
              }
              setState((s) => ({
                ...s,
                status: "cancelled",
                translatedPath: c.translated_file ?? s.translatedPath ?? null,
              }));
            } else if (type === "error") {
              const e = data as { message: string };
              setState((s) => ({ ...s, status: "error", error: e.message }));
            }
          },
        });
        // The stream can end without a terminal event (sidecar crash or
        // restart mid-job). Without this the overlay stays busy forever.
        // Functional update so a terminal event that did land wins. A pending
        // cancel resolves to `cancelled`: the stream closing means the backend
        // has stopped touching the artifact, which is all `cancelling` was
        // waiting for.
        setState((s) =>
          s.status === "running"
            ? {
                ...s,
                status: "error",
                error: "Translation stream ended unexpectedly",
              }
            : s.status === "cancelling"
              ? { ...s, status: "cancelled" }
              : s,
        );
      } catch (e) {
        setState((s) => ({ ...s, status: "error", error: (e as Error).message }));
      } finally {
        setActiveJob(null);
        abortRef.current = null;
      }
    },
    [
      setActiveJob,
      adoptArtifact,
      setTranslationTargetLang,
      setChunkProgress,
      bumpTranslatedReloadKey,
    ],
  );

  const cancel = useCallback(async () => {
    const jobId = useAppStore.getState().activeTranslationJob;
    if (!jobId) return;
    // Reflect the cancel intent immediately so the user isn't left staring at
    // a progress bar while the backend drains its in-flight chunk (1-3 s for
    // Argos, a few seconds for an LLM mid-request — neither can be hard-killed).
    // Deliberately `cancelling`, not `cancelled`: the drain runs
    // `cleanup_partial_artifacts()`, which unlinks every rolling version but
    // the newest, so nothing may act on the artifact until the terminal event
    // brings back the surviving one.
    setState((s) => ({ ...s, status: "cancelling" }));
    try {
      await api.post(`/translate/${jobId}/cancel`);
    } catch {
      // Best-effort — backend may already be tearing down.
    }
    // Deliberately do NOT abort the SSE stream here. We keep listening so the
    // terminal `cancelled` event lands with the partial rolling-PDF path; the
    // backend closes the stream itself once that event fires.
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
