import { describe, expect, it } from "vitest";

import { toReferenceRows } from "./rag-references";
import type { RagAnswer } from "@/hooks/useRagAsk";

/**
 * A `POST /rag/ask` answer exactly as `EnhancedRAGChain.answer_question`
 * serialises it — `pdf_references`, 1-indexed pages, one entry per retrieved
 * chunk. Copied from the shape in `rag_chain._create_pdf_references` rather
 * than trimmed to what the UI happens to read, so a divergence shows up here.
 */
const ANSWER: RagAnswer = {
  answer: "Attention layers replace recurrence.",
  pdf_references: [
    {
      type: "pdf",
      page: 1,
      text: "The dominant sequence transduction models are based on complex recurrent or convolutional neural networks…",
      confidence: 0.82,
      document_id: "attention",
      document_path: "D:\\Papers\\attention.pdf",
      chunk_id: "attention_0",
      has_equations: false,
      has_tables: false,
      has_figures: false,
    },
    {
      type: "pdf",
      page: 4,
      text: "Scaled dot-product attention",
      confidence: 0.61,
      document_id: "attention",
      chunk_id: "attention_9",
      has_equations: true,
      has_tables: false,
      has_figures: false,
    },
  ],
  quality_metrics: { total_sources: 2 },
  processing_time: 3.4,
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
    expect(first.detail).toBe(
      ANSWER.pdf_references![0].text!.slice(0, 180),
    );
    expect(second.label).toBe("Page 4");
    expect(second.page).toBe(4);
  });

  // Pages leave the sidecar 1-indexed (rag_chain._display_page). Page 0 would
  // mean the conversion was lost somewhere: PdfViewer.scrollToPage counts
  // from 1, so a 0 scrolls nowhere.
  it("never yields page 0 for a real reference", () => {
    for (const row of toReferenceRows(ANSWER)) {
      expect(row.page).toBeGreaterThan(0);
    }
  });

  it("gives every row a stable, unique key", () => {
    const keys = toReferenceRows(ANSWER).map((r) => r.key);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("handles an answer with no references", () => {
    expect(toReferenceRows({ answer: "I don't know." })).toEqual([]);
    expect(toReferenceRows({ answer: "…", pdf_references: [] })).toEqual([]);
  });

  // Chunk metadata is written by the indexer, not validated on read, so a
  // chunk stored before the current schema can come back without a page.
  // Better a placeholder than "Page undefined".
  it("falls back to a placeholder label when a page is missing", () => {
    const row = toReferenceRows({
      answer: "…",
      pdf_references: [{ type: "pdf", text: "orphan chunk" }],
    })[0];
    expect(row.label).toBe("Page ?");
    expect(row.page).toBeUndefined();
  });

  // `null` is the shape that actually arrives: `_display_page` returns None
  // for an unusable page rather than defaulting to 1, so the row must not
  // carry a page a click could scroll to.
  it("treats an explicitly null page as unknown", () => {
    const row = toReferenceRows({
      answer: "…",
      pdf_references: [{ type: "pdf", page: null, text: "orphan chunk" }],
    })[0];
    expect(row.label).toBe("Page ?");
    expect(row.page).toBeUndefined();
  });
});
