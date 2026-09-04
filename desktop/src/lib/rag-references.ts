/**
 * Turning a RAG answer's `pdf_references` into the rows the chat panel shows.
 *
 * Split out of `AssistantMessage` for the same reason as `export-pdf.ts` and
 * `translate-request.ts`: it's a pure decision, so it can be unit-tested under
 * the suite's `node` environment without a DOM.
 *
 * The field name is load-bearing. `rag_chain.answer_question` has always
 * returned `pdf_references`; the UI read `pdf_sources`, so the reference list
 * was permanently empty and the click-to-jump affordance unreachable (#13).
 * Page numbers arrive 1-indexed — converted once, in `rag_chain._display_page`
 * — because that is what `PdfViewer.scrollToPage` expects.
 */

import type { RagAnswer } from "@/hooks/useRagAsk";

export interface ReferenceRow {
  key: string;
  label: string;
  detail: string;
  page?: number;
}

/** How much of a chunk's text to show under its page label. */
const DETAIL_CHARS = 180;

export function toReferenceRows(answer: RagAnswer): ReferenceRow[] {
  return (answer.pdf_references ?? []).map((ref, i) => ({
    key: `pdf-${i}`,
    label: ref.page != null ? `Page ${ref.page}` : "Page ?",
    detail: (ref.text ?? "").slice(0, DETAIL_CHARS),
    page: ref.page,
  }));
}
