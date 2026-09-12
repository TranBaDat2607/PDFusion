/**
 * One `POST /rag/ask` question, as the chat panel tracks it.
 *
 * Pure, and split out of `useRagAsk` so the `node`-environment vitest suite can
 * hold it to the rule that matters here (#59): an answer belongs to the
 * document it was asked about. Switching PDFs mid-answer used to append the
 * previous document's answer to the new document's chat, with page links that
 * scrolled the new PDF.
 */

import type { components } from "@/lib/api-types";

/**
 * Generated from `api/sse_schemas.py` (see issue #27) — previously hand-typed,
 * and it was exactly that hand-copy declaring `pdf_sources` (alongside a
 * `web_sources` branch left over from the web research dropped in `35bca2c`)
 * that kept the reference list permanently empty (#13): the chain has always
 * returned `pdf_references`. `page` is 1-indexed — `rag_chain._display_page`
 * converts at that boundary, so it goes straight to `PdfViewer.scrollToPage`;
 * `null` when the chunk has no usable page.
 */
export type PdfReference = components["schemas"]["PdfReferencePayload"];

/** The `answer` / `done` SSE payload from `POST /rag/ask`. */
export type RagAnswer = components["schemas"]["AskResultPayload"];

/** `POST /rag/ask` was refused because the document has no ready chat index
 *  (HTTP 409). */
export const NOT_INDEXED = "not_indexed";

/** The question's index was missing or damaged, and the sidecar cleared it:
 *  the `code` of the `error` event (`api/routes/rag.py:INDEX_UNAVAILABLE`). */
export const INDEX_UNAVAILABLE = "index_unavailable";

export interface ActionEvent {
  id: number;
  description: string;
  status: "running" | "done" | "failed";
}

export interface AskState {
  status: "idle" | "asking" | "done" | "error";
  /** The document this question is about. `null` only while idle. */
  documentId: string | null;
  actions: ActionEvent[];
  message: string;
  progress: number;
  answer: RagAnswer | null;
  error?: string;
  /** Set when the chat panel should act on a failure rather than only show
   *  `error`: `NOT_INDEXED` or `INDEX_UNAVAILABLE`. */
  errorCode?: string;
}

/** The part of an `SseEvent` the reducer reads. */
export interface AskEvent {
  type: string;
  data: unknown;
}

export const IDLE_ASK: AskState = {
  status: "idle",
  documentId: null,
  actions: [],
  message: "",
  progress: 0,
  answer: null,
};

export function startAsk(documentId: string): AskState {
  return { ...IDLE_ASK, status: "asking", documentId };
}

export function failAsk(
  state: AskState,
  error: string,
  errorCode?: string | null,
): AskState {
  return errorCode
    ? { ...state, status: "error", error, errorCode }
    : { ...state, status: "error", error };
}

/** Fold one event from `/rag/ask/{job_id}/events` into the state. */
export function reduceAskEvent(state: AskState, event: AskEvent): AskState {
  switch (event.type) {
    case "progress": {
      const p = event.data as components["schemas"]["AskProgressPayload"];
      return {
        ...state,
        message: p.message ?? state.message,
        progress: p.progress ?? state.progress,
        actions: p.message
          ? [
              ...state.actions,
              {
                id: state.actions.length + 1,
                description: p.message,
                status: "done",
              },
            ]
          : state.actions,
      };
    }
    case "answer":
      return {
        ...state,
        status: "done",
        progress: 100,
        answer: event.data as RagAnswer,
      };
    case "done":
      // `done` repeats the answer (plus `elapsed_seconds`); the `answer` event
      // that preceded it, when there was one, stays the one shown.
      return {
        status: "done",
        documentId: state.documentId,
        actions: state.actions,
        message: state.message,
        progress: 100,
        answer: state.answer ?? (event.data as RagAnswer),
      };
    case "error": {
      const e = event.data as components["schemas"]["JobErrorPayload"];
      return failAsk(state, e.message, e.code);
    }
    default:
      return state;
  }
}

/** The stream ended with no terminal event: the sidecar died mid-answer. */
export function streamEnded(state: AskState): AskState {
  return state.status === "asking"
    ? failAsk(state, "Answer stream ended unexpectedly")
    : state;
}

/**
 * The finished answer, if it is about the document open now; otherwise `null`.
 * The chat panel appends only what this returns.
 */
export function answerForDocument(
  state: AskState,
  openDocumentId: string | null,
): RagAnswer | null {
  if (state.status !== "done" || state.answer === null) return null;
  if (openDocumentId === null || state.documentId !== openDocumentId) {
    return null;
  }
  return state.answer;
}

/**
 * Whether the question failed because the open document has no usable chat
 * index, so the chat panel should index it again (#31). Only when the question
 * was about the document open now: the panel may have moved on to another.
 */
export function needsReindex(
  state: AskState,
  openDocumentId: string | null,
): boolean {
  return (
    state.status === "error" &&
    openDocumentId !== null &&
    state.documentId === openDocumentId &&
    (state.errorCode === NOT_INDEXED || state.errorCode === INDEX_UNAVAILABLE)
  );
}
