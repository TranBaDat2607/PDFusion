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

import type { components } from "@/lib/api-types";

type AskRequest = components["schemas"]["AskRequest"];

export interface AskBodyInput {
  question: string;
  /** The open document. Not nullable: `null` used to ask across every indexed
   *  document, which is how chat answered from PDFs other than the open one
   *  (#59). The sidecar refuses a request without one. */
  documentId: string;
  /** The language to answer in: the toolbar's "To" language. Omitted from the
   *  body when null/undefined, and the sidecar then answers in the configured
   *  default. Answers used to be Vietnamese whatever was chosen (#31). */
  targetLang?: string | null;
}

export function buildAskBody(input: AskBodyInput): AskRequest {
  return {
    question: input.question,
    document_id: input.documentId,
    // `AskRequest.max_pdf_sources` has a server-side default (5) and could be
    // omitted on the wire, but openapi-typescript marks a field with a
    // concrete (non-null) default as always-present in the generated type,
    // since that's the right read for *response* fields — the majority of
    // what's generated here. Spelling out the same default the backend would
    // apply anyway keeps this request body's behavior identical either way.
    max_pdf_sources: 5,
    // Spread rather than assigned, as in `translate-request.ts`, so an unset
    // language is absent from the body rather than sent as `undefined`. The
    // cast is a boundary one: the value comes from `LanguageCode` via config.
    ...(input.targetLang
      ? { target_lang: input.targetLang as AskRequest["target_lang"] }
      : {}),
  };
}
