import { useCallback, useRef, useState } from "react";

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

  const start = useCallback(async (filePath: string) => {
    setState({ ...INITIAL, status: "indexing", stage: "Submitting…" });

    let jobId: string;
    try {
      const accepted = await api.post<{ job_id: string }>("/rag/index", {
        file_path: filePath,
      });
      jobId = accepted.job_id;
    } catch (e) {
      setState({ ...INITIAL, status: "error", error: (e as Error).message });
      return;
    }

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
            setState((s) => ({
              ...s,
              stage: p.stage ?? s.stage,
              progress: p.progress ?? s.progress,
            }));
          } else if (type === "done") {
            const c = data as components["schemas"]["IndexDonePayload"];
            setState({
              status: "ready",
              stage: "Ready",
              progress: 100,
              documentId: c.document_id,
              chunks: c.chunks,
            });
          } else if (type === "error") {
            const e = data as components["schemas"]["JobErrorPayload"];
            setState((s) => ({ ...s, status: "error", error: e.message }));
          }
        },
      });
      // Stream ended without a terminal event (sidecar died mid-index).
      setState((s) =>
        s.status === "indexing"
          ? { ...s, status: "error", error: "Index stream ended unexpectedly" }
          : s,
      );
    } catch (e) {
      setState((s) => ({
        ...s,
        status: "error",
        error: (e as Error).message,
      }));
    } finally {
      abortRef.current = null;
    }
  }, []);

  const reset = useCallback(() => setState(INITIAL), []);

  return { state, start, reset };
}
