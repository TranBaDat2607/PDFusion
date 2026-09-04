import { useCallback, useRef, useState } from "react";

import { api } from "@/lib/api-client";
import { buildAskBody } from "@/lib/ask-request";
import { streamEvents } from "@/lib/sse";

export interface ActionEvent {
  id: number;
  description: string;
  status: "running" | "done" | "failed";
}

/** One retrieved chunk from `rag_chain._create_pdf_references`. */
export interface PdfReference {
  text?: string;
  /** 1-indexed — `rag_chain._display_page` converts at that boundary, so it
   *  goes straight to `PdfViewer.scrollToPage`. `null` when the chunk has no
   *  usable page; the sidecar sends that rather than guessing one. */
  page?: number | null;
}

/**
 * The `answer` / `done` SSE payload from `POST /rag/ask`, narrowed to what the
 * UI reads — the sidecar also sends `quality_metrics`, `sources_used`,
 * `processing_time` and `timestamp`, so this is not a mirror of
 * `EnhancedRAGChain.answer_question` and shouldn't be maintained as one.
 *
 * The key is load-bearing: the chain has always returned `pdf_references`, and
 * this file declaring `pdf_sources` (alongside a `web_sources` branch left
 * over from the web research dropped in `35bca2c`) is what kept the reference
 * list empty (#13).
 */
export interface RagAnswer {
  answer: string;
  pdf_references?: PdfReference[];
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
}

export function useRagAsk() {
  const [state, setState] = useState<AskState>(INITIAL);
  const abortRef = useRef<AbortController | null>(null);

  const ask = useCallback(async (params: AskParams) => {
    setState({ ...INITIAL, status: "asking" });

    let jobId: string;
    try {
      const accepted = await api.post<{ job_id: string }>(
        "/rag/ask",
        buildAskBody(params),
      );
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
