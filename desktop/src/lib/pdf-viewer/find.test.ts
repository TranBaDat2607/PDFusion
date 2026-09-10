import { describe, expect, it } from "vitest";

import {
  buildPageIndex,
  findInText,
  firstMatchFrom,
  foldQuery,
  matchToItemRanges,
  pageText,
  stepMatch,
  type FindTextItem,
} from "./find";

function find(items: FindTextItem[], query: string) {
  const index = buildPageIndex(items);
  return findInText(index.text, foldQuery(query)).map((match) =>
    matchToItemRanges(index, match),
  );
}

describe("matching", () => {
  it("ignores case and Vietnamese diacritics", () => {
    const items = [{ str: "Tiếng Việt" }];
    expect(find(items, "viet")).toHaveLength(1);
    expect(find(items, "VIỆT")).toHaveLength(1);
    expect(find(items, "tieng viet")).toHaveLength(1);
  });

  it("treats đ as d, which Unicode doesn't decompose", () => {
    expect(find([{ str: "Đang tải" }], "dang tai")).toHaveLength(1);
  });

  it("matches decomposed source text", () => {
    // Some PDFs carry "ệ" as e + combining circumflex + combining dot below.
    // Escaped so an editor normalizing the file can't quietly change the test.
    // The range still ends after the "t": marks inside a match are covered.
    expect(find([{ str: "Vie\u0302\u0323t" }], "viet")).toEqual([
      [{ item: 0, from: 0, to: 6 }],
    ]);
  });

  it("collapses runs of whitespace", () => {
    expect(find([{ str: "foo   bar" }], "foo bar")).toEqual([
      [{ item: 0, from: 0, to: 9 }],
    ]);
  });

  it("finds a phrase that wraps onto the next line", () => {
    const items = [{ str: "hello", hasEOL: true }, { str: "world" }];
    expect(find(items, "hello world")).toEqual([
      [
        { item: 0, from: 0, to: 5 },
        { item: 1, from: 0, to: 5 },
      ],
    ]);
  });

  it("joins items with no line break between them", () => {
    // pdf.js splits words into several items whenever the font or spacing
    // changes mid-word.
    expect(find([{ str: "trans" }, { str: "lation" }], "translation")).toEqual([
      [
        { item: 0, from: 0, to: 5 },
        { item: 1, from: 0, to: 6 },
      ],
    ]);
  });

  it("returns every non-overlapping match", () => {
    expect(find([{ str: "aaaa" }], "aa")).toEqual([
      [{ item: 0, from: 0, to: 2 }],
      [{ item: 0, from: 2, to: 4 }],
    ]);
  });

  it("finds nothing for an empty or blank query", () => {
    expect(foldQuery("   ")).toBe("");
    expect(find([{ str: "anything" }], "")).toEqual([]);
  });
});

describe("item numbering", () => {
  it("skips marked-content entries, which have no text div", () => {
    const items = [{ str: "a" }, {}, { str: "b" }];
    expect(find(items, "b")).toEqual([[{ item: 1, from: 0, to: 1 }]]);
  });

  it("counts empty strings, which TextLayer still creates a div for", () => {
    const items = [{ str: "a" }, { str: "" }, { str: "b" }];
    expect(find(items, "b")).toEqual([[{ item: 2, from: 0, to: 1 }]]);
  });
});

describe("pageText", () => {
  it("is exactly the index's text, so counts and highlights agree", () => {
    const items = [
      { str: "  Tiếng  Việt", hasEOL: true },
      {},
      { str: "" },
      { str: "ĐANG\ttải" },
    ];
    expect(pageText(items)).toBe(buildPageIndex(items).text);
    expect(pageText(items)).toBe("tieng viet dang tai");
  });
});

describe("stepMatch", () => {
  it("starts at the first match going forward and the last going back", () => {
    expect(stepMatch(5, null, 1)).toBe(0);
    expect(stepMatch(5, null, -1)).toBe(4);
  });

  it("wraps around both ends", () => {
    expect(stepMatch(5, 4, 1)).toBe(0);
    expect(stepMatch(5, 0, -1)).toBe(4);
  });

  it("has nowhere to go with no matches", () => {
    expect(stepMatch(0, null, 1)).toBeNull();
  });
});

describe("firstMatchFrom", () => {
  const matches = [
    { page: 2, start: 0, end: 1 },
    { page: 5, start: 0, end: 1 },
    { page: 5, start: 4, end: 5 },
    { page: 9, start: 0, end: 1 },
  ];

  it("picks the first match on the page or after it", () => {
    expect(firstMatchFrom(matches, 5)).toBe(1);
    expect(firstMatchFrom(matches, 6)).toBe(3);
  });

  it("wraps to the first match when there is none further on", () => {
    expect(firstMatchFrom(matches, 10)).toBe(0);
  });

  it("has nothing to pick with no matches", () => {
    expect(firstMatchFrom([], 1)).toBeNull();
  });
});
