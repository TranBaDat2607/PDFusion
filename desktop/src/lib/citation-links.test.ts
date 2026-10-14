import { describe, expect, it } from "vitest";

import { pageFromCitationHref } from "./citation-links";

describe("pageFromCitationHref", () => {
  // `rag/citations.py` writes every checked citation as `[p. N](#page=N)`.
  it("reads the page of a citation link", () => {
    expect(pageFromCitationHref("#page=7")).toBe(7);
    expect(pageFromCitationHref("#page=120")).toBe(120);
  });

  // Anything else is an ordinary link, or not a page the viewer has.
  it("is undefined for anything that is not a citation of a real page", () => {
    expect(pageFromCitationHref(undefined)).toBeUndefined();
    expect(pageFromCitationHref("https://example.com/#page=3")).toBeUndefined();
    expect(pageFromCitationHref("#page=0")).toBeUndefined();
    expect(pageFromCitationHref("#page=abc")).toBeUndefined();
    expect(pageFromCitationHref("#page=3x")).toBeUndefined();
  });
});
