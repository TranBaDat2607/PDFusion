import { describe, expect, it } from "vitest";

import {
  DECODE_RETAIN,
  renewRetained,
  retainReleased,
} from "./decode-retention";

/** Release each page in turn, starting from `start`, and return the final list
 *  along with everything evicted on the way. */
function releaseAll(
  pages: number[],
  limit?: number,
  start: number[] = [],
): { retained: number[]; evicted: number[] } {
  let retained = start;
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

describe("renewRetained", () => {
  it("moves the pages that are back on screen to the end, in order", () => {
    expect(renewRetained([9, 3, 7, 4], { first: 3, last: 5 })).toEqual([
      9, 7, 3, 4,
    ]);
  });

  it("keeps every page on the list, so none loses its cleanup", () => {
    // The list is the record of what still owes a `cleanup()`. Dropping a page
    // from it strands whatever that page decoded until the document is
    // destroyed, because only a release puts a page back on.
    const retained = [1, 2, 3, 4];
    expect(renewRetained(retained, { first: 1, last: 4 })).toHaveLength(4);
  });

  it("keeps everything where it is when no page is on screen", () => {
    // An empty viewport or a document still loading is not a reason to reorder
    // the queue.
    expect(renewRetained([9, 3, 7], null)).toEqual([9, 3, 7]);
  });

  it("does not mutate the list it was given", () => {
    const retained = [1, 2, 3];
    renewRetained(retained, { first: 1, last: 3 });
    expect(retained).toEqual([1, 2, 3]);
  });

  it("still evicts a renewed page the pump never reached", () => {
    // The hole this ordering closes: page 4 came back into the window, so it
    // left the queue; the scroll carried on before it was re-rendered, so
    // nothing ever released it again. Renewing rather than forgetting keeps it
    // evictable, and its decode is handed back like any other page's.
    const renewed = renewRetained([4, 5, 6], { first: 4, last: 4 });
    const { retained, evicted } = releaseAll([7, 8, 9], 3, renewed);
    expect(evicted).toContain(4);
    expect(retained).toEqual([7, 8, 9]);
  });
});
