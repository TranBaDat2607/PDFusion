import { describe, expect, it } from "vitest";

import {
  emptyMask,
  finishMask,
  maskAfterArtifact,
  maskTransition,
  paneMask,
  STREAM_LINES,
  STREAM_MS,
  streamBand,
  type PaneMask,
} from "./translation-mask";

const translatedOf = (
  mask: Parameters<typeof paneMask>[0],
  path: string | null,
) => paneMask(mask, path)?.translated;

describe("emptyMask", () => {
  it("translates nothing on any path", () => {
    expect(translatedOf(emptyMask(), "a.pdf")).toEqual(new Set());
    expect(translatedOf(emptyMask(), null)).toEqual(new Set());
    expect(paneMask(emptyMask(), "a.pdf")?.drawable).toEqual(new Set());
  });
});

describe("paneMask", () => {
  it("does not mask at all when no run is in progress", () => {
    expect(paneMask(null, "a.pdf")).toBeNull();
    expect(paneMask(null, null)).toBeNull();
  });

  it("blanks everything for a path no version has", () => {
    const mask = maskAfterArtifact(emptyMask(), "a.pdf", [1, 2]);
    for (const path of ["other.pdf", null]) {
      const pane = paneMask(mask, path)!;
      expect(pane.drawable).toEqual(new Set());
      expect(pane.translated).toEqual(new Set());
    }
  });

  it("draws exactly the translated pages of a file mid-run", () => {
    const mask = maskAfterArtifact(emptyMask(), "a.pdf", [1, 2]);
    const pane = paneMask(mask, "a.pdf")!;
    expect(pane.drawable).toEqual(new Set([1, 2]));
    expect(pane.translated).toEqual(new Set([1, 2]));
  });

  it("lifts the mask only on the final file", () => {
    const mask = finishMask(
      maskAfterArtifact(emptyMask(), "a.pdf", [1, 2]),
      "a.pdf",
    )!;
    const pane = paneMask(mask, "a.pdf")!;
    expect(pane.drawable).toBeNull();
    expect(pane.translated).toEqual(new Set([1, 2]));
  });

  it("keeps a non-final file masked after the run finished", () => {
    let mask = maskAfterArtifact(emptyMask(), "v1", [1, 1]);
    mask = maskAfterArtifact(mask, "v2", [2, 2]);
    // The pane is still loading v2 and shows v1, which holds original text.
    const finished = finishMask(mask, "v2")!;
    expect(paneMask(finished, "v1")!.drawable).toEqual(new Set([1]));
    const last = paneMask(finished, "v2")!;
    expect(last.drawable).toBeNull();
    expect(last.translated).toEqual(new Set([1, 2]));
  });

  it("keeps the placeholder document masked after the run finished", () => {
    const finished = finishMask(
      maskAfterArtifact(emptyMask(), "v1", [1, 1]),
      "v1",
    )!;
    expect(paneMask(finished, "placeholder.pdf")!.drawable).toEqual(new Set());
    expect(paneMask(finished, null)!.drawable).toEqual(new Set());
  });
});

describe("finishMask", () => {
  it("returns null without a mask", () => {
    expect(finishMask(null, "a.pdf")).toBeNull();
  });

  it("returns null when the run has no result file", () => {
    const mask = maskAfterArtifact(emptyMask(), "a.pdf", [1, 2]);
    expect(finishMask(mask, null)).toBeNull();
  });

  it("records the final path and leaves the versions alone", () => {
    const mask = maskAfterArtifact(emptyMask(), "a.pdf", [1, 2]);
    const finished = finishMask(mask, "a.pdf")!;
    expect(finished.final).toBe("a.pdf");
    expect(finished.versions).toEqual(mask.versions);
  });

  it("does not mutate its input", () => {
    const mask = maskAfterArtifact(emptyMask(), "a.pdf", [1, 2]);
    const snapshot = JSON.parse(JSON.stringify(mask));
    finishMask(mask, "a.pdf");
    expect(mask).toEqual(snapshot);
    expect("final" in mask).toBe(false);
  });
});

describe("maskAfterArtifact", () => {
  it("records exactly the first chunk's pages on the first file", () => {
    const mask = maskAfterArtifact(emptyMask(), "a.pdf", [30, 30]);
    expect(mask.versions).toEqual([{ path: "a.pdf", pages: [30] }]);
  });

  it("carries earlier chunks into later files, whatever the order", () => {
    let mask = maskAfterArtifact(emptyMask(), "a.pdf", [30, 30]);
    mask = maskAfterArtifact(mask, "b.pdf", [31, 31]);
    mask = maskAfterArtifact(mask, "c.pdf", [29, 29]);
    expect(mask.versions[mask.versions.length - 1].pages).toEqual([29, 30, 31]);
  });

  it("keeps an older version's own pages", () => {
    let mask = maskAfterArtifact(emptyMask(), "a.pdf", [1, 2]);
    mask = maskAfterArtifact(mask, "b.pdf", [3, 4]);
    expect(mask.versions).toEqual([
      { path: "a.pdf", pages: [1, 2] },
      { path: "b.pdf", pages: [1, 2, 3, 4] },
    ]);
  });

  it("accepts a reversed span", () => {
    const mask = maskAfterArtifact(emptyMask(), "a.pdf", [7, 5]);
    expect(mask.versions[0].pages).toEqual([5, 6, 7]);
  });

  it("inherits the newest pages for a span of unknown extent", () => {
    let mask = maskAfterArtifact(emptyMask(), "a.pdf", [1, 2]);
    mask = maskAfterArtifact(mask, "b.pdf", null);
    expect(mask.versions[1]).toEqual({ path: "b.pdf", pages: [1, 2] });
  });

  it("records no pages for an unknown span on an empty mask", () => {
    const mask = maskAfterArtifact(emptyMask(), "a.pdf", null);
    expect(mask.versions).toEqual([{ path: "a.pdf", pages: [] }]);
  });

  it("merges in place when the newest path is adopted again", () => {
    let mask = maskAfterArtifact(emptyMask(), "a.pdf", [1, 2]);
    mask = maskAfterArtifact(mask, "a.pdf", [5, 5]);
    expect(mask.versions).toHaveLength(1);
    expect(mask.versions[0]).toEqual({ path: "a.pdf", pages: [1, 2, 5] });
  });

  it("keeps pages ascending and unique when spans overlap", () => {
    let mask = maskAfterArtifact(emptyMask(), "a.pdf", [3, 5]);
    mask = maskAfterArtifact(mask, "b.pdf", [4, 6]);
    expect(mask.versions[1].pages).toEqual([3, 4, 5, 6]);
  });

  it("keeps every version, so a pane that fell far behind still draws its own pages", () => {
    // Each new path cancels the load in flight, so a pane can stay on an old
    // version for many chunks. A run is at most 100 pages, so this is cheap.
    let mask = emptyMask();
    for (let i = 1; i <= 20; i++) {
      mask = maskAfterArtifact(mask, `v${i}.pdf`, [i, i]);
    }
    expect(mask.versions).toHaveLength(20);
    expect(paneMask(mask, "v1.pdf")!.drawable).toEqual(new Set([1]));
  });

  it("returns a mask with no final property, the run continuing", () => {
    const finished = { versions: [{ path: "a.pdf", pages: [1] }], final: "a.pdf" };
    const next = maskAfterArtifact(finished, "b.pdf", [2, 2]);
    expect("final" in next).toBe(false);
  });

  it("does not mutate the input mask", () => {
    const before = maskAfterArtifact(emptyMask(), "a.pdf", [1, 2]);
    const snapshot = JSON.parse(JSON.stringify(before));
    maskAfterArtifact(before, "b.pdf", [3, 3]);
    maskAfterArtifact(before, "a.pdf", [9, 9]);
    expect(before).toEqual(snapshot);
  });
});

const pane = (
  drawable: number[] | null,
  translated: number[],
): PaneMask => ({
  drawable: drawable && new Set(drawable),
  translated: new Set(translated),
});

describe("maskTransition", () => {
  it("hides rendered pages outside the drawable set, sorted", () => {
    const result = maskTransition(pane([1, 2, 3], [1, 2, 3]), pane([2], [2]), [
      9, 3, 1, 2,
    ]);
    expect(result.hide).toEqual([1, 3, 9]);
  });

  it("streams newly translated pages, rendered or not", () => {
    const result = maskTransition(
      pane([1, 2], [1, 2]),
      pane([1, 2, 7, 5], [1, 2, 7, 5]),
      [1, 2],
    );
    expect(result.stream).toEqual([5, 7]);
    expect(result.hide).toEqual([]);
  });

  it("streams nothing that was already translated", () => {
    const result = maskTransition(pane([1, 2], [1, 2]), pane([1, 2], [1, 2]), [
      1, 2,
    ]);
    expect(result).toEqual({ hide: [], stream: [], forget: false });
  });

  it("hides every rendered page when a run starts, streaming nothing", () => {
    const result = maskTransition(null, pane([], []), [3, 1, 2]);
    expect(result.hide).toEqual([1, 2, 3]);
    expect(result.stream).toEqual([]);
  });

  it("streams only the newly translated page when the final file shows", () => {
    const result = maskTransition(
      pane([1, 2], [1, 2]),
      pane(null, [1, 2, 3]),
      [1, 2, 3, 4, 5],
    );
    expect(result).toEqual({ hide: [], stream: [3], forget: true });
  });

  it("hides and streams nothing once the mask is gone", () => {
    const result = maskTransition(pane([1], [1]), null, [1, 2, 3]);
    expect(result).toEqual({ hide: [], stream: [], forget: true });
  });

  // `forget`: drop pages still waiting to stream. They belong to the run that
  // queued them, and a page queued by one run must not sweep in during another
  // or long after its own run ended.
  it("keeps pages waiting to stream while a run continues", () => {
    expect(
      maskTransition(pane([1], [1]), pane([1, 2], [1, 2]), []).forget,
    ).toBe(false);
  });

  it("forgets pages waiting to stream when a run starts", () => {
    expect(maskTransition(null, pane([], []), []).forget).toBe(true);
    // Straight from the previous run's result, with no unmasked state between.
    expect(maskTransition(pane(null, [1, 2]), pane([], []), []).forget).toBe(
      true,
    );
  });
});

describe("streamBand", () => {
  const bands = Array.from({ length: STREAM_LINES }, (_, i) =>
    streamBand(i, STREAM_LINES),
  );

  it("starts the top band straight away", () => {
    expect(bands[0].delay).toBe(0);
  });

  it("starts the bands top to bottom", () => {
    for (let i = 1; i < bands.length; i++) {
      expect(bands[i].delay).toBeGreaterThan(bands[i - 1].delay);
    }
  });

  it("finishes the whole page at STREAM_MS", () => {
    const end = Math.max(...bands.map((b) => b.delay + b.duration));
    expect(end).toBeCloseTo(STREAM_MS);
  });

  it("starts each band before the one above it has finished", () => {
    for (let i = 1; i < bands.length; i++) {
      const above = bands[i - 1];
      expect(bands[i].delay).toBeLessThan(above.delay + above.duration);
    }
  });
});
