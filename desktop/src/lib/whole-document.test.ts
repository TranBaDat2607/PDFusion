import { describe, expect, it } from "vitest";

import { WHOLE_DOCUMENT_CHOICES, wholeDocumentChoice } from "./whole-document";

describe("whole-document reading choices", () => {
  it("offers off, the measured default, and two larger budgets, smallest first", () => {
    expect(WHOLE_DOCUMENT_CHOICES.map((c) => c.tokens)).toEqual([0, 11_500, 45_000, 90_000]);
    expect(WHOLE_DOCUMENT_CHOICES.find((c) => c.tokens === 11_500)?.label).toMatch(/default/i);
  });

  it("names a saved budget by its choice", () => {
    expect(wholeDocumentChoice(45_000).tokens).toBe(45_000);
    expect(wholeDocumentChoice(0).label).toMatch(/off/i);
  });

  it("a budget set elsewhere (the config file) is shown as it is, not snapped to a choice", () => {
    const custom = wholeDocumentChoice(30_000);
    expect(custom.tokens).toBe(30_000);
    expect(custom.label).toContain("30,000");
  });
});
