import { useCallback, useRef, useState } from "react";

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

export interface TranslationState {
  status: "idle" | "running" | "done" | "error" | "cancelled";
  stage: string;
  progress: number; // 0-100
  message: string;
  error?: string;
  translatedPath?: string | null;
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

  const start = useCallback(
    async (filePath: string) => {
      setState({ ...INITIAL, status: "running", stage: "Starting…" });

      let jobId: string;
      try {
        const accepted = await api.post<{ job_id: string }>("/translate", {
          file_path: filePath,
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
        await streamEvents<ProgressUpdate | CompletionPayload | { message: string }>({
          path: `/translate/${jobId}/events`,
          signal: controller.signal,
          onEvent: ({ type, data }) => {
            if (type === "progress") {
              const p = data as ProgressUpdate;
              setState((s) => ({
                ...s,
                stage: p.stage ?? s.stage,
                progress: p.progress_percent ?? s.progress,
                message: p.message ?? s.message,
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
    [setActiveJob, setTranslatedPdfPath],
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

  return { state, start, cancel, reset };
}
