import { describe, expect, it } from "vitest";

import { firstPdfPath } from "./file-drop";

describe("firstPdfPath", () => {
  it("picks the first PDF among the dropped files", () => {
    expect(
      firstPdfPath(["C:\\notes.txt", "D:\\Papers\\a.pdf", "D:\\Papers\\b.pdf"]),
    ).toBe("D:\\Papers\\a.pdf");
  });

  it("matches the extension case-insensitively", () => {
    expect(firstPdfPath(["scan.PDF"])).toBe("scan.PDF");
  });

  it("does not mistake a folder named pdf for a document", () => {
    expect(firstPdfPath(["C:\\pdf\\notes.txt", "C:\\pdf"])).toBeNull();
  });

  it("has nothing to open when nothing was dropped", () => {
    expect(firstPdfPath([])).toBeNull();
  });
});
