import { describe, expect, it } from "vitest";

import {
  CHANGE_LOG_LIMIT,
  accumulateChanges,
  accumulateSince,
  appendArtifactChange,
  latestSeq,
  pagesToRefresh,
  type ArtifactChange,
} from "./artifact-swap";

describe("accumulateChanges", () => {
  it("records a chunk's pages", () => {
    expect(accumulateChanges(null, [5, 7])).toEqual(new Set([5, 6, 7]));
  });

  it("keeps an abandoned load's pages when the next chunk lands", () => {
    // v2 was still loading when v3 arrived; v3 differs from the v1 on screen
    // by both chunks.
    const afterV2 = accumulateChanges(null, [5, 7]);
    expect(accumulateChanges(afterV2, [20, 20])).toEqual(
      new Set([5, 6, 7, 20]),
    );
  });

  it("treats a change of unknown extent as every page", () => {
    expect(accumulateChanges(new Set([3]), null)).toBe("all");
    expect(accumulateChanges(null, undefined)).toBe("all");
  });

  it("stays at every page once it gets there", () => {
    expect(accumulateChanges("all", [1, 1])).toBe("all");
  });

  it("accepts a range given backwards", () => {
    expect(accumulateChanges(null, [3, 2])).toEqual(new Set([2, 3]));
  });

  it("does not modify the set it was given", () => {
    const pending = new Set([1]);
    accumulateChanges(pending, [2, 2]);
    expect(pending).toEqual(new Set([1]));
  });
});

describe("pagesToRefresh", () => {
  it("repaints only rendered pages that changed, in page order", () => {
    expect(pagesToRefresh([9, 4, 5, 6], new Set([6, 5, 30]))).toEqual([5, 6]);
  });

  it("repaints every rendered page when the extent is unknown", () => {
    expect(pagesToRefresh(new Set([3, 1, 2]), "all")).toEqual([1, 2, 3]);
  });

  it("has nothing to do when no changed page is on screen", () => {
    expect(pagesToRefresh([1, 2, 3], new Set([40]))).toEqual([]);
  });
});

describe("the change log", () => {
  function logOf(...hints: Array<[number, number] | null>): ArtifactChange[] {
    return hints.reduce<ArtifactChange[]>(
      (log, hint) => appendArtifactChange(log, hint),
      [],
    );
  }

  it("numbers entries from 1", () => {
    const log = logOf([1, 1], [4, 6]);
    expect(log.map((entry) => entry.seq)).toEqual([1, 2]);
    expect(latestSeq(log)).toBe(2);
    expect(latestSeq([])).toBe(0);
  });

  it("keeps only the newest entries", () => {
    const hints = Array.from(
      { length: CHANGE_LOG_LIMIT + 5 },
      (_, i): [number, number] => [i + 1, i + 1],
    );
    const log = logOf(...hints);
    expect(log).toHaveLength(CHANGE_LOG_LIMIT);
    expect(log[0].seq).toBe(6);
    expect(latestSeq(log)).toBe(CHANGE_LOG_LIMIT + 5);
  });

  it("folds in every chunk a batched render delivered at once", () => {
    // Chunks 2 and 3 landed in one render; the viewer had seen chunk 1.
    const log = logOf([1, 1], [5, 5], [9, 10]);
    expect(accumulateSince(null, log, 1)).toEqual({
      pending: new Set([5, 9, 10]),
      seen: 3,
    });
  });

  it("adds to what an overtaken load had already collected", () => {
    const log = logOf([1, 1], [5, 5]);
    expect(accumulateSince(new Set([1]), log, 1)).toEqual({
      pending: new Set([1, 5]),
      seen: 2,
    });
  });

  it("changes nothing when there is nothing new", () => {
    const log = logOf([1, 1]);
    const pending = new Set([7]);
    expect(accumulateSince(pending, log, 1)).toEqual({ pending, seen: 1 });
  });

  it("repaints everything when the entries it missed were trimmed", () => {
    const hints = Array.from(
      { length: CHANGE_LOG_LIMIT + 3 },
      (_, i): [number, number] => [i + 1, i + 1],
    );
    const log = logOf(...hints);
    expect(accumulateSince(null, log, 1)).toEqual({
      pending: "all",
      seen: CHANGE_LOG_LIMIT + 3,
    });
  });
});
