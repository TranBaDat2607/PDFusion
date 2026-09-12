import { useCallback, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api-client";
import { buildAskBody } from "@/lib/ask-request";
import {
  IDLE_ASK,
  NOT_INDEXED,
  failAsk,
  reduceAskEvent,
  startAsk,
  streamEnded,
  type AskState,
} from "@/lib/rag-ask";
import { streamJobEvents } from "@/lib/sse";

export type {
  ActionEvent,
  AskState,
  PdfReference,
  RagAnswer,
} from "@/lib/rag-ask";

interface AskParams {
  question: string;
  /** The open document. A question is always about exactly one (#59). */
  documentId: string;
  /** The language to answer in; the configured default when unset. */
  targetLang?: string | null;
}

export function useRagAsk() {
  const [state, setState] = useState<AskState>(IDLE_ASK);
  const abortRef = useRef<AbortController | null>(null);
  // Bumped by every ask, abort and reset. A callback from a stream whose
  // generation is no longer current belongs to a question nobody is waiting
  // for — the previous document's answer, or the "ended unexpectedly" check
  // that runs once an aborted stream returns — so it must not touch state.
  const generationRef = useRef(0);

  const abort = useCallback(() => {
    generationRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const ask = useCallback(async (params: AskParams) => {
    abort();
    const generation = generationRef.current;
    const update = (next: (s: AskState) => AskState) => {
      if (generationRef.current === generation) setState(next);
    };
    setState(startAsk(params.documentId));

    let jobId: string;
    try {
      const accepted = await api.post<{ job_id: string }>(
        "/rag/ask",
        buildAskBody(params),
      );
      jobId = accepted.job_id;
    } catch (e) {
      // A 409 means the document has no ready index; the panel indexes it.
      const code =
        e instanceof ApiError && e.status === 409 ? NOT_INDEXED : null;
      update((s) => failAsk(s, (e as Error).message, code));
      return;
    }
    if (generationRef.current !== generation) return;

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamJobEvents({
        buildPath: (lastEventId) =>
          `/rag/ask/${jobId}/events${lastEventId ? `?last_seq=${lastEventId}` : ""}`,
        signal: controller.signal,
        onEvent: (event) => update((s) => reduceAskEvent(s, event)),
      });
      // Stream ended without a terminal event (sidecar died mid-answer).
      update(streamEnded);
    } catch (e) {
      update((s) => failAsk(s, (e as Error).message));
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  }, [abort]);

  const reset = useCallback(() => {
    abort();
    setState(IDLE_ASK);
  }, [abort]);

  return { state, ask, abort, reset };
}
