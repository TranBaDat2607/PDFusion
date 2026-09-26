import { describe, expect, it } from "vitest";

import { toReferenceRows } from "./rag-references";
import type { PdfReference, RagAnswer } from "@/hooks/useRagAsk";

/** A complete `PdfReferencePayload`, with sensible defaults for every field
 *  `toReferenceRows` doesn't read — only `page`/`text` vary per test. */
function makeReference(overrides: Partial<PdfReference> = {}): PdfReference {
  return {
    type: "pdf",
    page: 1,
    text: "chunk text",
    confidence: 0.9,
    document_id: "doc1",
    document_path: "C:/docs/doc1.pdf",
    chunk_id: "doc1-chunk-0",
    has_equations: false,
    has_tables: false,
    has_figures: false,
    bbox: null,
    ...overrides,
  };
}

/** A complete `AskResultPayload`, with every field the wire format always
 *  carries (see `sse_schemas.AskResultPayload`) — only `pdf_references`
 *  varies per test; the rest are the values a successful answer sends. */
function makeAnswer(overrides: Partial<RagAnswer> = {}): RagAnswer {
  return {
    answer: "…",
    elapsed_seconds: 1.0,
    processing_time: 1.0,
    sources_used: { pdf_sources: 1 },
    timestamp: "2026-09-09T00:00:00",
    provider: null,
    model: null,
    ...overrides,
  };
}

/**
 * A `POST /rag/ask` answer as `EnhancedRAGChain.answer_question` serialises it,
 * carrying the two fields the rows are built from. Pages arrive 1-indexed —
 * `rag_chain._display_page` converts before they reach the wire.
 */
const ANSWER: RagAnswer = makeAnswer({
  answer: "Attention layers replace recurrence.",
  pdf_references: [
    makeReference({
      page: 1,
      text: "The dominant sequence transduction models are based on complex recurrent or convolutional neural networks…",
    }),
    makeReference({ page: 4, text: "Scaled dot-product attention" }),
  ],
});

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
    expect(toReferenceRows(makeAnswer())).toEqual([]);
    expect(toReferenceRows(makeAnswer({ pdf_references: [] }))).toEqual([]);
  });

  // `_display_page` returns null rather than guessing a page for a chunk
  // whose metadata has none. `_create_pdf_references` always includes the
  // `page` key, so `null` — not an absent key — is the only real shape;
  // `toReferenceRows`'s `ref.page ?? undefined` treats them identically
  // regardless. That value must not yield a page a click could scroll to —
  // `PdfViewer.scrollToPage` would take it at face value.
  it("treats a null page as unknown", () => {
    const ref = makeReference({ page: null, text: "orphan chunk" });
    const [row] = toReferenceRows(makeAnswer({ pdf_references: [ref] }));
    expect(row.label).toBe("Page ?");
    expect(row.page).toBeUndefined();
  });
});
