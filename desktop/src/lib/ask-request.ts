/**
 * Building a `POST /rag/ask` request body.
 *
 * Small enough to inline, kept separate for the same reason as
 * `translate-request.ts`: it's the one place the wire shape is decided, and
 * the `node`-environment vitest suite can hold it to `api/schemas.py:AskRequest`.
 *
 * That drift is what this module exists to prevent. The chat input used to send
 * `include_web_research` and `use_deep_search` alongside the question; deep
 * search and web research were deleted in `35bca2c`, so `AskRequest` has no
 * such fields and FastAPI dropped them silently — two toggles in the UI that
 * changed nothing (#14).
 */

export interface AskBodyInput {
  question: string;
  /** `null` asks across every indexed document. */
  documentId: string | null;
}

export function buildAskBody(input: AskBodyInput): Record<string, unknown> {
  return {
    question: input.question,
    document_id: input.documentId,
  };
}
