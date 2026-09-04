import { describe, expect, it } from "vitest";

import { toReferenceRows } from "./rag-references";
import type { PdfReference, RagAnswer } from "@/hooks/useRagAsk";

/**
 * A `POST /rag/ask` answer as `EnhancedRAGChain.answer_question` serialises it,
 * carrying the two fields the rows are built from. Pages arrive 1-indexed —
 * `rag_chain._display_page` converts before they reach the wire.
 */
const ANSWER: RagAnswer = {
  answer: "Attention layers replace recurrence.",
  pdf_references: [
    {
      page: 1,
      text: "The dominant sequence transduction models are based on complex recurrent or convolutional neural networks…",
    },
    { page: 4, text: "Scaled dot-product attention" },
  ],
};

describe("toReferenceRows", () => {
  // The bug this fixes: the payload key. The backend has always sent
  // `pdf_references`; reading `pdf_sources` made every answer look
  // source-less, so the list never rendered and page-jumping was unreachable.
  it("reads the references the sidecar actually sends", () => {
    expect(toReferenceRows(ANSWER)).toHaveLength(2);
  });

  it("labels each row with its page and previews the chunk", () => {
    const [first, second] = toReferenceRows(ANSWER);
    expect(first.label).toBe("Page 1");
    expect(first.page).toBe(1);
    expect(first.detail).toBe(ANSWER.pdf_references![0].text);
    expect(second.label).toBe("Page 4");
    expect(second.page).toBe(4);
  });

  it("handles an answer with no references", () => {
    expect(toReferenceRows({ answer: "I don't know." })).toEqual([]);
    expect(toReferenceRows({ answer: "…", pdf_references: [] })).toEqual([]);
  });

  // `_display_page` returns null rather than guessing a page for a chunk whose
  // metadata has none; a chunk stored before the current schema can arrive
  // with no `page` key at all. Neither may yield a page a click could scroll
  // to — PdfViewer.scrollToPage would take it at face value.
  it.each<PdfReference>([
    { text: "orphan chunk" },
    { page: null, text: "orphan chunk" },
  ])("treats %o as an unknown page", (ref) => {
    const [row] = toReferenceRows({ answer: "…", pdf_references: [ref] });
    expect(row.label).toBe("Page ?");
    expect(row.page).toBeUndefined();
  });
});
