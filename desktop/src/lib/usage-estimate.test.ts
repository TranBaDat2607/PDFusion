import { describe, expect, it } from "vitest";

import {
  estimateDetails,
  estimateLabel,
  formatTokens,
  type TranslationEstimate,
} from "./usage-estimate";

const ESTIMATE: TranslationEstimate = {
  page_count: 120,
  pages_selected: 21,
  paragraphs: 340,
  input_tokens: 70_210,
  output_tokens: 11_700,
};

describe("formatTokens", () => {
  it.each([
    [0, "0"],
    [950, "950"],
    [1000, "1k"],
    [1234, "1.3k"],
    [9_900, "9.9k"],
    [9_999, "10k"],
    [12_345, "13k"],
    [999_000, "999k"],
    [999_999, "1M"],
    [1_000_000, "1M"],
    [1_234_567, "1.3M"],
  ])("%d → %s", (tokens, label) => {
    expect(formatTokens(tokens)).toBe(label);
  });
});

describe("estimateLabel", () => {
  it("counts what is sent and what comes back", () => {
    expect(estimateLabel(ESTIMATE)).toBe("≈82k tokens");
  });
});

describe("estimateDetails", () => {
  it("says what was counted, and how roughly", () => {
    expect(estimateDetails(ESTIMATE)).toEqual([
      "About 340 paragraphs on 21 pages, each sent to the service on its own.",
      "≈71k tokens sent, instructions included; ≈12k back.",
      "A rough count from the PDF's text. The service's own count will differ.",
    ]);
  });

  it("says paragraph and page for one", () => {
    const [first] = estimateDetails({ ...ESTIMATE, paragraphs: 1, pages_selected: 1 });
    expect(first).toBe("About 1 paragraph on 1 page, each sent to the service on its own.");
  });
});
