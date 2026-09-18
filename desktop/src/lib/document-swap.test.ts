import { describe, expect, it } from "vitest";

import { shouldConfirmSwap, swapConfirmCopy } from "./document-swap";

describe("shouldConfirmSwap", () => {
  it("opens straight away when nothing is running", () => {
    expect(shouldConfirmSwap(false, "C:\\b.pdf", "C:\\a.pdf")).toBe(false);
  });

  it("asks before discarding a run for a different document", () => {
    expect(shouldConfirmSwap(true, "C:\\b.pdf", "C:\\a.pdf")).toBe(true);
  });

  it("leaves the running document alone when it is re-opened", () => {
    // "Open with PDFusion" on the file the user is already watching. Asking
    // here would offer to kill the very run they can see making progress.
    expect(shouldConfirmSwap(true, "C:\\a.pdf", "C:\\a.pdf")).toBe(false);
  });

  it("asks when a run started before any document was recorded", () => {
    expect(shouldConfirmSwap(true, "C:\\b.pdf", null)).toBe(true);
  });
});

describe("swapConfirmCopy", () => {
  it("names both documents and how far the run got", () => {
    const copy = swapConfirmCopy({
      incomingPath: "C:\\Papers\\other.pdf",
      currentPath: "C:\\Papers\\paper.pdf",
      progress: 37.6,
    });
    expect(copy.description).toContain("paper.pdf is 38% translated.");
    expect(copy.description).toContain("Opening other.pdf cancels that run");
    expect(copy.action).toBe("Discard and open other.pdf");
    expect(copy.dismiss).toBe("Keep translating");
  });

  it("reads posix paths the same way", () => {
    const copy = swapConfirmCopy({
      incomingPath: "/home/me/other.pdf",
      currentPath: "/home/me/paper.pdf",
      progress: 5,
    });
    expect(copy.description).toContain("paper.pdf is 5% translated.");
    expect(copy.action).toBe("Discard and open other.pdf");
  });

  it("does not claim 0% when the run has barely started", () => {
    const copy = swapConfirmCopy({
      incomingPath: "/home/me/other.pdf",
      currentPath: "/home/me/paper.pdf",
      progress: 0,
    });
    expect(copy.description).toContain("paper.pdf is still being translated.");
    expect(copy.description).not.toContain("0%");
  });

  it("stays sensible with no current document recorded", () => {
    const copy = swapConfirmCopy({
      incomingPath: "/home/me/other.pdf",
      currentPath: null,
      progress: 42,
    });
    expect(copy.description).toContain("A translation is still running.");
    expect(copy.description).toContain("Opening other.pdf");
  });

  it("clamps a progress value outside 0-100", () => {
    const copy = swapConfirmCopy({
      incomingPath: "b.pdf",
      currentPath: "a.pdf",
      progress: 140,
    });
    expect(copy.description).toContain("a.pdf is 100% translated.");
  });
});
