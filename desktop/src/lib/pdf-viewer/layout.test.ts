import { describe, expect, it } from "vitest";

import {
  CONTENT_PADDING,
  MAX_CANVAS_PIXELS,
  PAGE_GAP,
  ZOOM_MAX,
  ZOOM_MIN,
  canvasScale,
  fitWidthZoom,
  mostVisiblePage,
  offsetForAnchor,
  offsetForPage,
  pageGeometry,
  parsePageInput,
  renderPriority,
  renderWindow,
  scrollAnchor,
  slotSize,
  visibleRange,
  zoomIn,
  zoomOut,
} from "./layout";

const PAGE = { width: 100, height: 200 };
const THREE_PAGES = [PAGE, PAGE, PAGE];

describe("pageGeometry", () => {
  it("stacks slots with padding around them and a gap between them", () => {
    const geo = pageGeometry(THREE_PAGES, 1);
    expect(geo.tops).toEqual([16, 232, 448]);
    expect(geo.heights).toEqual([200, 200, 200]);
    expect(geo.contentHeight).toBe(448 + 200 + CONTENT_PADDING);
    expect(PAGE_GAP).toBe(16);
  });

  it("sizes slots through slotSize, so the JSX and the model round the same way", () => {
    const sizes = [{ width: 612, height: 792 }];
    const zoom = 1.2 ** 3;
    expect(pageGeometry(sizes, zoom).heights[0]).toBe(
      slotSize(sizes[0], zoom).height,
    );
  });

  it("gives an empty document only its padding", () => {
    expect(pageGeometry([], 1)).toEqual({
      tops: [],
      heights: [],
      contentHeight: 2 * CONTENT_PADDING,
    });
  });
});

describe("visibleRange", () => {
  const geo = pageGeometry(THREE_PAGES, 1);

  it("finds the single page at the top", () => {
    expect(visibleRange(geo, 0, 100)).toEqual({ first: 1, last: 1 });
  });

  it("skips a page whose bottom edge is already above the viewport", () => {
    // 220 is in the gap: page 1 ends at 216, page 2 starts at 232.
    expect(visibleRange(geo, 220, 100)).toEqual({ first: 2, last: 2 });
  });

  it("spans every page a tall viewport shows", () => {
    expect(visibleRange(geo, 0, 700)).toEqual({ first: 1, last: 3 });
  });

  it("settles on the last page when scrolled past the end", () => {
    expect(visibleRange(geo, 10_000, 100)).toEqual({ first: 3, last: 3 });
  });

  it("has nothing to report for an empty document", () => {
    expect(visibleRange(pageGeometry([], 1), 0, 100)).toBeNull();
  });
});

describe("mostVisiblePage", () => {
  const geo = pageGeometry(THREE_PAGES, 1);

  it("picks the page covering the most of the viewport", () => {
    // Page 1 shows 66 px (150–216), page 2 shows 118 px (232–350).
    expect(mostVisiblePage(geo, 150, 200)).toBe(2);
  });

  it("prefers the earlier page on a tie", () => {
    // 92 px of each: 124–216 and 232–324.
    expect(mostVisiblePage(geo, 124, 200)).toBe(1);
  });

  it("reports the last page at the bottom of the document", () => {
    // Zoomed out, pages 2 and 3 are both fully visible at the bottom, so the
    // tie-break alone could never reach the last page.
    const small = pageGeometry(THREE_PAGES, 0.5);
    const bottom = small.contentHeight - 300;
    expect(mostVisiblePage(small, bottom, 300)).toBe(3);
  });
});

describe("renderWindow", () => {
  it("keeps three pages either side of the visible ones", () => {
    expect(renderWindow({ first: 5, last: 6 }, 50)).toEqual({
      first: 2,
      last: 9,
    });
  });

  it("clamps to the document", () => {
    expect(renderWindow({ first: 1, last: 1 }, 4)).toEqual({
      first: 1,
      last: 4,
    });
  });
});

describe("renderPriority", () => {
  it("renders the visible pages first, then the nearest buffer pages", () => {
    expect(
      renderPriority({ first: 5, last: 6 }, { first: 2, last: 9 }),
    ).toEqual([5, 6, 7, 4, 8, 3, 9, 2]);
  });

  it("keeps going on one side after the other side runs out", () => {
    expect(
      renderPriority({ first: 1, last: 1 }, { first: 1, last: 4 }),
    ).toEqual([1, 2, 3, 4]);
  });
});

describe("scroll anchoring", () => {
  it("round-trips at the same zoom", () => {
    const geo = pageGeometry(THREE_PAGES, 1);
    const anchor = scrollAnchor(geo, 332)!;
    expect(anchor).toEqual({ page: 2, fraction: 0.5 });
    expect(offsetForAnchor(geo, anchor)).toBe(332);
  });

  it("keeps the reader halfway down the same page after zooming", () => {
    const before = pageGeometry(THREE_PAGES, 1);
    const after = pageGeometry(THREE_PAGES, 2);
    const anchor = scrollAnchor(before, 332)!;
    // Page 2 now starts at 432 and is 400 px tall.
    expect(offsetForAnchor(after, anchor)).toBe(632);
  });
});

describe("offsetForPage", () => {
  const geo = pageGeometry(THREE_PAGES, 1);

  it("puts page 1 at the very top", () => {
    expect(offsetForPage(geo, 1)).toBe(0);
  });

  it("leaves the gap above a later page in view", () => {
    expect(offsetForPage(geo, 3)).toBe(448 - PAGE_GAP);
  });

  it("clamps a page past the end to the last page", () => {
    expect(offsetForPage(geo, 99)).toBe(offsetForPage(geo, 3));
  });
});

describe("canvasScale", () => {
  const LETTER = { width: 612, height: 792 };

  it("renders at zoom × dpr when that fits", () => {
    expect(canvasScale(LETTER, 1, 2)).toBe(2);
  });

  it("caps the backing store at extreme zoom", () => {
    const scale = canvasScale(LETTER, 5, 2);
    const pixels = LETTER.width * LETTER.height * scale * scale;
    expect(scale).toBeLessThan(10);
    expect(pixels).toBeLessThanOrEqual(MAX_CANVAS_PIXELS + 1);
    expect(pixels).toBeGreaterThan(MAX_CANVAS_PIXELS * 0.99);
  });
});

describe("zoom steps", () => {
  it("steps by 20%", () => {
    expect(zoomIn(1)).toBeCloseTo(1.2);
    expect(zoomOut(1.2)).toBeCloseTo(1);
  });

  it("stops at the limits", () => {
    expect(zoomIn(4.5)).toBe(ZOOM_MAX);
    expect(zoomOut(0.21)).toBe(ZOOM_MIN);
  });

  it("fits a page to the scroller less the column padding", () => {
    expect(fitWidthZoom(600, 600 + 2 * CONTENT_PADDING)).toBe(1);
    expect(fitWidthZoom(0, 800)).toBe(1);
  });
});

describe("parsePageInput", () => {
  it("accepts a page number, ignoring surrounding space", () => {
    expect(parsePageInput("12", 50)).toBe(12);
    expect(parsePageInput(" 7 ", 50)).toBe(7);
  });

  it("clamps past the end to the last page", () => {
    expect(parsePageInput("999", 50)).toBe(50);
  });

  it.each(["", "0", "-3", "abc", "3.5", "1e3"])("rejects %j", (text) => {
    expect(parsePageInput(text, 50)).toBeNull();
  });

  it("has no pages to go to in an empty document", () => {
    expect(parsePageInput("1", 0)).toBeNull();
  });
});
