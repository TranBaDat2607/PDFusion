import { describe, expect, it } from "vitest";

import { WHOLE_DOCUMENT_CHOICES, wholeDocumentChoice } from "./whole-document";

describe("whole-document reading choices", () => {
  it("offers off, the measured default, and two larger budgets, smallest first", () => {
    expect(WHOLE_DOCUMENT_CHOICES.map((c) => c.tokens)).toEqual([0, 11_500, 45_000, 80_000]);
    expect(WHOLE_DOCUMENT_CHOICES.find((c) => c.tokens === 11_500)?.label).toMatch(/default/i);
  });

  it("never offers a budget the sidecar refuses, and the largest is its maximum", () => {
    // Past 80,000 estimated tokens a Vietnamese document overflows a 128k window (#106).
    const tokens = WHOLE_DOCUMENT_CHOICES.map((c) => c.tokens);
    expect(tokens.every((t) => t <= 80_000)).toBe(true);
    expect(Math.max(...tokens)).toBe(80_000);
    expect(WHOLE_DOCUMENT_CHOICES.find((c) => c.tokens === 80_000)?.label).toContain("~70 pages");
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
