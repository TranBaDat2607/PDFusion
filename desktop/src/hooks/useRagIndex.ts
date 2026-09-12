import { useCallback, useRef, useState, type SetStateAction } from "react";

import { api } from "@/lib/api-client";
import type { components } from "@/lib/api-types";
import { streamJobEvents } from "@/lib/sse";

export interface IndexState {
  status: "idle" | "indexing" | "ready" | "error";
  stage: string;
  progress: number;
  documentId: string | null;
  chunks: number | null;
  error?: string;
}

const INITIAL: IndexState = {
  status: "idle",
  stage: "",
  progress: 0,
  documentId: null,
  chunks: null,
};

export function useRagIndex() {
  const [state, setState] = useState<IndexState>(INITIAL);
  const abortRef = useRef<AbortController | null>(null);
  // Bumped by every start and reset, for the same reason as `useRagAsk`'s: the
  // previous document's index job keeps streaming after the next `start`, and
  // its `done` used to mark the panel ready — with the previous document's id,
  // so the first question about the new PDF was asked of the old one (#59).
  const generationRef = useRef(0);

  const abort = useCallback(() => {
    generationRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const start = useCallback(async (filePath: string) => {
    abort();
    const generation = generationRef.current;
    const update = (next: SetStateAction<IndexState>) => {
      if (generationRef.current === generation) setState(next);
    };
    setState({ ...INITIAL, status: "indexing", stage: "Submitting…" });

    let jobId: string;
    try {
      const accepted = await api.post<{ job_id: string }>("/rag/index", {
        file_path: filePath,
      });
      jobId = accepted.job_id;
    } catch (e) {
      update({ ...INITIAL, status: "error", error: (e as Error).message });
      return;
    }
    if (generationRef.current !== generation) return;

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamJobEvents({
        buildPath: (lastEventId) =>
          `/rag/index/${jobId}/events${lastEventId ? `?last_seq=${lastEventId}` : ""}`,
        signal: controller.signal,
        onEvent: ({ type, data }) => {
          if (type === "progress") {
            const p = data as components["schemas"]["IndexProgressPayload"];
            update((s) => ({
              ...s,
              stage: p.stage ?? s.stage,
              progress: p.progress ?? s.progress,
              // The first event names the document, before the embedding model
              // has loaded, so the panel can show its saved chat meanwhile. The
              // status stays `indexing`: nothing can be asked until `done`.
              documentId: p.document_id ?? s.documentId,
            }));
          } else if (type === "done") {
            const c = data as components["schemas"]["IndexDonePayload"];
            update({
              status: "ready",
              stage: "Ready",
              progress: 100,
              documentId: c.document_id,
              chunks: c.chunks,
            });
          } else if (type === "error") {
            const e = data as components["schemas"]["JobErrorPayload"];
            update((s) => ({ ...s, status: "error", error: e.message }));
          }
        },
      });
      // Stream ended without a terminal event (sidecar died mid-index).
      update((s) =>
        s.status === "indexing"
          ? { ...s, status: "error", error: "Index stream ended unexpectedly" }
          : s,
      );
    } catch (e) {
      update((s) => ({
        ...s,
        status: "error",
        error: (e as Error).message,
      }));
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  }, [abort]);

  const reset = useCallback(() => {
    abort();
    setState(INITIAL);
  }, [abort]);

  return { state, start, reset };
}
