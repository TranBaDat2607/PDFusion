import { useCallback, useRef, useState } from "react";

import { api } from "@/lib/api-client";
import { streamEvents } from "@/lib/sse";

export interface ActionEvent {
  id: number;
  description: string;
  status: "running" | "done" | "failed";
}

export interface RagAnswer {
  answer: string;
  pdf_sources?: Array<{
    text?: string;
    page?: number;
    document_id?: string;
    score?: number;
  }>;
  web_sources?: Array<{
    url?: string;
    title?: string;
    snippet?: string;
    source_type?: string;
  }>;
  citations?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export interface AskState {
  status: "idle" | "asking" | "done" | "error";
  actions: ActionEvent[];
  message: string;
  progress: number;
  answer: RagAnswer | null;
  error?: string;
}

const INITIAL: AskState = {
  status: "idle",
  actions: [],
  message: "",
  progress: 0,
  answer: null,
};

let actionCounter = 0;

interface AskParams {
  question: string;
  documentId: string | null;
  includeWebResearch: boolean;
  useDeepSearch: boolean;
}

export function useRagAsk() {
  const [state, setState] = useState<AskState>(INITIAL);
  const abortRef = useRef<AbortController | null>(null);

  const ask = useCallback(async (params: AskParams) => {
    setState({ ...INITIAL, status: "asking" });

    let jobId: string;
    try {
      const accepted = await api.post<{ job_id: string }>("/rag/ask", {
        question: params.question,
        document_id: params.documentId,
        include_web_research: params.includeWebResearch,
        use_deep_search: params.useDeepSearch,
      });
      jobId = accepted.job_id;
    } catch (e) {
      setState({ ...INITIAL, status: "error", error: (e as Error).message });
      return;
    }

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamEvents({
        path: `/rag/ask/${jobId}/events`,
        signal: controller.signal,
        onEvent: ({ type, data }) => {
          if (type === "progress") {
            const p = data as { message?: string; progress?: number };
            setState((s) => ({
              ...s,
              message: p.message ?? s.message,
              progress: p.progress ?? s.progress,
              actions: p.message
                ? [
                    ...s.actions,
                    {
                      id: ++actionCounter,
                      description: p.message,
                      status: "done",
                    },
                  ]
                : s.actions,
            }));
          } else if (type === "answer") {
            const ans = data as RagAnswer;
            setState((s) => ({
              ...s,
              status: "done",
              progress: 100,
              answer: ans,
            }));
          } else if (type === "done") {
            const ans = data as RagAnswer;
            setState((s) => ({
              status: "done",
              actions: s.actions,
              message: s.message,
              progress: 100,
              answer: s.answer ?? ans,
            }));
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
      // Stream ended without a terminal event (sidecar died mid-answer).
      setState((s) =>
        s.status === "asking"
          ? { ...s, status: "error", error: "Answer stream ended unexpectedly" }
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

  return { state, ask, reset };
}
