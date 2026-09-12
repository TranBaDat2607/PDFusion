import { describe, expect, it } from "vitest";

import { describeDocument, type ChatDocument } from "./chat-documents";

function makeDocument(overrides: Partial<ChatDocument> = {}): ChatDocument {
  return {
    document_id: "abc123",
    display_name: "paper.pdf",
    path: "C:/papers/paper.pdf",
    size_bytes: 1536,
    page_count: 12,
    chunk_count: 40,
    question_count: 3,
    last_opened_at: "2026-09-12T00:00:00+00:00",
    ...overrides,
  };
}

describe("describeDocument", () => {
  it("says what is stored for a document", () => {
    expect(describeDocument(makeDocument())).toBe(
      "12 pages · 1.5 KB · indexed, 40 chunks · 3 questions",
    );
  });

  it("says when nothing can be asked of it yet", () => {
    expect(
      describeDocument(makeDocument({ chunk_count: null, question_count: 0 })),
    ).toBe("12 pages · 1.5 KB · not indexed · no questions");
  });

  it("leaves out a page count it doesn't know, and counts one in the singular", () => {
    expect(
      describeDocument(
        makeDocument({ page_count: null, chunk_count: 1, question_count: 1 }),
      ),
    ).toBe("1.5 KB · indexed, 1 chunk · 1 question");
  });
});
