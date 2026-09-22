import { describe, expect, it } from "vitest";

import {
  DECODE_RETAIN,
  forgetRetained,
  retainReleased,
} from "./decode-retention";

/** Release each page in turn, returning the final list and everything evicted
 *  along the way. */
function releaseAll(
  pages: number[],
  limit?: number,
): { retained: number[]; evicted: number[] } {
  let retained: number[] = [];
  const evicted: number[] = [];
  for (const page of pages) {
    const step = retainReleased(retained, page, limit);
    retained = step.retained;
    evicted.push(...step.evicted);
  }
  return { retained, evicted };
}

describe("retainReleased", () => {
  it("keeps a release and evicts nothing while under the limit", () => {
    expect(releaseAll([4, 5, 6], 3)).toEqual({
      retained: [4, 5, 6],
      evicted: [],
    });
  });

  it("evicts the least recently released page once over the limit", () => {
    expect(releaseAll([4, 5, 6, 7], 3)).toEqual({
      retained: [5, 6, 7],
      evicted: [4],
    });
  });

  it("evicts in release order when several pages go at once", () => {
    expect(releaseAll([1, 2, 3, 4, 5], 2)).toEqual({
      retained: [4, 5],
      evicted: [1, 2, 3],
    });
  });

  it("moves a page released again to the newest slot instead of duplicating it", () => {
    // Scrolled past page 4, back over it, then past it again: it is the
    // freshest release, so page 5 is the one that should go first.
    expect(releaseAll([4, 5, 6, 4, 7], 3)).toEqual({
      retained: [6, 4, 7],
      evicted: [5],
    });
  });

  it("cleans up immediately when nothing is retained", () => {
    expect(retainReleased([], 9, 0)).toEqual({ retained: [], evicted: [9] });
  });

  it("does not mutate the list it was given", () => {
    const retained = [1, 2];
    retainReleased(retained, 3, 2);
    expect(retained).toEqual([1, 2]);
  });

  it("defaults to DECODE_RETAIN pages", () => {
    const pages = Array.from({ length: DECODE_RETAIN + 1 }, (_, i) => i + 1);
    expect(releaseAll(pages).evicted).toEqual([1]);
  });
});

describe("forgetRetained", () => {
  it("drops the pages that are back on screen and keeps the rest in order", () => {
    expect(forgetRetained([9, 3, 7, 4], { first: 3, last: 5 })).toEqual([9, 7]);
  });

  it("keeps everything when no page is on screen", () => {
    // An empty viewport or a document still loading is not a reason to throw
    // decoded pages away.
    expect(forgetRetained([9, 3, 7], null)).toEqual([9, 3, 7]);
  });

  it("does not mutate the list it was given", () => {
    const retained = [1, 2, 3];
    forgetRetained(retained, { first: 1, last: 3 });
    expect(retained).toEqual([1, 2, 3]);
  });
});
