/**
 * Turning a RAG answer's `pdf_references` into the rows the chat panel shows.
 *
 * Split out of `AssistantMessage` for the same reason as `export-pdf.ts` and
 * `translate-request.ts`: it's a pure decision, so it can be unit-tested under
 * the suite's `node` environment without a DOM.
 *
 * This module owns the payload's field name. `rag_chain.answer_question` has
 * always returned `pdf_references`; the UI read `pdf_sources`, so the list was
 * permanently empty and click-to-jump unreachable (#13). Page semantics belong
 * to the sidecar — see `rag_chain._display_page`.
 */

import type { ReferenceItem } from "@/components/chat/ReferenceList";
import type { RagAnswer } from "@/hooks/useRagAsk";

export function toReferenceRows(answer: RagAnswer): ReferenceItem[] {
  return (answer.pdf_references ?? []).map((ref, i) => {
    // `null` and absent both mean "unknown page": say so in the label, and
    // leave `page` unset so the click guard won't scroll anywhere.
    const page = ref.page ?? undefined;
    return {
      key: `pdf-${i}`,
      label: `Page ${page ?? "?"}`,
      detail: ref.text,
      page,
    };
  });
}
