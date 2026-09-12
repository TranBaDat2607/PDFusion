import { describe, expect, it } from "vitest";

import { buildAskBody } from "./ask-request";

describe("buildAskBody", () => {
  it("sends the question and the document to scope it to", () => {
    expect(
      buildAskBody({ question: "What is the ablation?", documentId: "paper" }),
    ).toEqual({
      question: "What is the ablation?",
      document_id: "paper",
      max_pdf_sources: 5,
    });
  });

  it("sends the language to answer in", () => {
    expect(
      buildAskBody({ question: "hi", documentId: "paper", targetLang: "ja" }),
    ).toMatchObject({ target_lang: "ja" });
  });

  // The sidecar applies the configured default only when the key is absent.
  it("omits an unset language", () => {
    for (const targetLang of [undefined, null, ""]) {
      const body = buildAskBody({ question: "hi", documentId: "paper", targetLang });
      expect(body).not.toHaveProperty("target_lang");
    }
  });

  // The regression this guards: `AskRequest` accepts `question`,
  // `document_id`, `max_pdf_sources` and `target_lang`, and nothing else.
  // Sending fields the schema doesn't declare is not an error — FastAPI drops
  // them — so a toggle wired to one looks functional while doing nothing at all.
  it("sends nothing the sidecar's AskRequest doesn't declare", () => {
    const body = buildAskBody({
      question: "hi",
      documentId: "paper",
      targetLang: "vi",
    });
    expect(Object.keys(body).sort()).toEqual([
      "document_id",
      "max_pdf_sources",
      "question",
      "target_lang",
    ]);
    expect(body).not.toHaveProperty("include_web_research");
    expect(body).not.toHaveProperty("use_deep_search");
  });
});
