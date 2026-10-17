import { describe, expect, it } from "vitest";

import {
  MAX_RANGES,
  countPages,
  describeSelection,
  formatPageRanges,
  parsePageRanges,
} from "./page-range";

describe("parsePageRanges", () => {
  it.each(["", "   ", "all", "All"])("reads %j as the whole document", (text) => {
    expect(parsePageRanges(text, 120)).toEqual({ ok: true, ranges: null });
  });

  it("reads single pages and ranges", () => {
    expect(parsePageRanges("1-20, 35", 120)).toEqual({
      ok: true,
      ranges: [
        [1, 20],
        [35, 35],
      ],
    });
  });

  it("takes the dashes and separators people actually type", () => {
    expect(parsePageRanges("1 – 3; 7—9 12", 120)).toEqual({
      ok: true,
      ranges: [
        [1, 3],
        [7, 9],
        [12, 12],
      ],
    });
  });

  it("sorts, and joins ranges that overlap or touch", () => {
    expect(parsePageRanges("30-40, 5, 1-4, 35-50", 120)).toEqual({
      ok: true,
      ranges: [
        [1, 5],
        [30, 50],
      ],
    });
  });

  it("runs an open range to the last page", () => {
    expect(parsePageRanges("100-", 120)).toEqual({ ok: true, ranges: [[100, 120]] });
  });

  it("needs the page count for an open range", () => {
    const parsed = parsePageRanges("100-", null);
    expect(parsed.ok).toBe(false);
  });

  it("treats every page as the whole document", () => {
    expect(parsePageRanges("1-60, 61-120", 120)).toEqual({ ok: true, ranges: null });
  });

  it("leaves pages past an unknown end to the sidecar", () => {
    expect(parsePageRanges("900", null)).toEqual({ ok: true, ranges: [[900, 900]] });
  });

  it.each([
    ["abc", `"abc" isn't a page or a range, like 35 or 1-20.`],
    ["1-2-3", `"1-2-3" isn't a page or a range, like 35 or 1-20.`],
    ["-5", `"-5" isn't a page or a range, like 35 or 1-20.`],
    ["0-3", "Page numbers start at 1."],
    ["9-3", "9-3 ends before it starts."],
    ["118-121", "This PDF has 120 pages, so there is no page 121."],
  ])("refuses %j", (text, error) => {
    expect(parsePageRanges(text, 120)).toEqual({ ok: false, error });
  });

  it("says page, not pages, for a one-page PDF", () => {
    expect(parsePageRanges("2", 1)).toEqual({
      ok: false,
      error: "This PDF has 1 page, so there is no page 2.",
    });
  });

  it("refuses more ranges than the sidecar accepts", () => {
    const text = Array.from({ length: MAX_RANGES + 1 }, (_, i) => 2 * i + 1).join(",");
    const parsed = parsePageRanges(text, 5000);
    expect(parsed.ok).toBe(false);
  });
});

describe("formatting and counting", () => {
  it("counts pages across ranges", () => {
    expect(countPages([[1, 20], [35, 35]])).toBe(21);
  });

  it("formats with an en dash, or the dash asked for", () => {
    expect(formatPageRanges([[1, 20], [35, 35]])).toBe("1–20, 35");
    expect(formatPageRanges([[1, 20], [35, 35]], "-")).toBe("1-20, 35");
  });

  it("round-trips what it formats for editing", () => {
    const ranges: [number, number][] = [[3, 9], [12, 12]];
    expect(parsePageRanges(formatPageRanges(ranges, "-"), 20)).toEqual({
      ok: true,
      ranges,
    });
  });
});

describe("describeSelection", () => {
  it("says how much will be translated", () => {
    expect(describeSelection(parsePageRanges("", 120), 120)).toBe("of 120");
    expect(describeSelection(parsePageRanges("1-20, 35", 120), 120)).toBe("21 of 120");
  });

  it("says nothing it can't know", () => {
    expect(describeSelection(parsePageRanges("", null), null)).toBeNull();
    expect(describeSelection(parsePageRanges("x", 120), 120)).toBeNull();
  });
});
