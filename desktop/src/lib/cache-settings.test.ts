import { describe, expect, it } from "vitest";

import {
  clearConfirmation,
  describeCleared,
  formatMegabytes,
} from "./cache-settings";

describe("formatMegabytes", () => {
  it.each([
    [0, "0 MB"],
    [0.0001, "1 KB"],
    [0.5, "512 KB"],
    [3.26, "3.3 MB"],
    [212.4, "212 MB"],
  ])("shows %s MB as %s", (mb, shown) => {
    expect(formatMegabytes(mb)).toBe(shown);
  });
});

describe("clearConfirmation", () => {
  it("says clearing PDFs keeps the paragraphs", () => {
    const { title, description } = clearConfirmation("pdf");
    expect(title).toMatch(/PDF cache/);
    expect(description).toMatch(/Translated paragraphs are kept/);
  });

  it("says clearing paragraphs keeps the PDFs", () => {
    const { title, description } = clearConfirmation("paragraph");
    expect(title).toMatch(/paragraph cache/);
    expect(description).toMatch(/Translated PDFs are kept/);
  });
});

describe("describeCleared", () => {
  it.each([
    [1, "pdf", "all", "Removed 1 translated PDF"],
    [3, "pdf", "all", "Removed 3 translated PDFs"],
    [1, "paragraph", "all", "Removed 1 paragraph"],
    [0, "paragraph", "expired", "Removed 0 expired paragraphs"],
  ] as const)("%s from %s (%s)", (removed, target, scope, text) => {
    expect(describeCleared(removed, target, scope)).toBe(text);
  });
});
