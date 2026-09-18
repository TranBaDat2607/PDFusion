import { describe, expect, it } from "vitest";

import {
  applyChunkReady,
  chunksReady,
  describeChunk,
  pagesRemaining,
  pagesReady,
  pluralizePages,
  type ChunkProgress,
  type ChunkReadyLike,
} from "./translation-progress";

/** One `chunk_ready` for a 50-page PDF translated one page per chunk. */
function page(n: number, totalPages = 50): ChunkReadyLike {
  return {
    chunk_index: n - 1,
    total_chunks: totalPages,
    pages_in_chunk: [n, n],
    total_pages: totalPages,
  };
}

/** A backend configured for 3-page chunks: chunk 0 covers pages 1-3. */
function threePageChunk(index: number, totalPages = 10): ChunkReadyLike {
  const first = index * 3 + 1;
  return {
    chunk_index: index,
    total_chunks: Math.ceil(totalPages / 3),
    pages_in_chunk: [first, Math.min(first + 2, totalPages)],
    total_pages: totalPages,
  };
}

/**
 * The single synthetic `chunk_ready` a PDF-cache hit emits. Its span is always
 * the whole document — the viewer swaps the file rather than repainting a
 * chunk — while `pages_to_translate` is what the served file has translated.
 */
function cacheHit(pagesTranslated: number, totalPages = 120): ChunkReadyLike {
  return {
    chunk_index: 0,
    total_chunks: 1,
    pages_in_chunk: [1, totalPages],
    total_pages: totalPages,
    pages_to_translate: pagesTranslated,
    cache_hit: true,
  };
}

function accumulate(events: ChunkReadyLike[]): ChunkProgress {
  return events.reduce<ChunkProgress | null>(
    (acc, e) => applyChunkReady(acc, e),
    null,
  )!;
}

describe("applyChunkReady", () => {
  // The reported bug: start translating while looking at page 30 of 50 and
  // the first chunk to land is page 30's. Reading its last page as a running
  // total claimed 30 pages were done after one.
  it("counts what completed, not how far into the document it was", () => {
    const progress = accumulate([page(30)]);
    expect(pagesReady(progress)).toBe(1);
    expect(chunksReady(progress)).toBe(1);
    expect(pagesRemaining(progress)).toBe(49);
  });

  it("accumulates across chunks that arrive out of order", () => {
    const progress = accumulate([page(30), page(31), page(29), page(1)]);
    expect(pagesReady(progress)).toBe(4);
    expect(pagesRemaining(progress)).toBe(46);
  });

  // An SSE re-attach replays events. Counting them would overshoot the total.
  it("is idempotent for a redelivered chunk", () => {
    const progress = accumulate([page(30), page(31), page(30)]);
    expect(pagesReady(progress)).toBe(2);
    expect(chunksReady(progress)).toBe(2);
  });

  // With 3-page chunks, total_chunks (4) is not the page count (10).
  it("counts pages, not chunks, for multi-page chunks", () => {
    const progress = accumulate([threePageChunk(0), threePageChunk(1)]);
    expect(chunksReady(progress)).toBe(2);
    expect(pagesReady(progress)).toBe(6);
    expect(progress.totalPages).toBe(10);
    expect(pagesRemaining(progress)).toBe(4);
  });

  it("counts a short trailing chunk by its real length", () => {
    // 10 pages in 3-page chunks: the last one holds page 10 alone.
    const progress = accumulate([0, 1, 2, 3].map((i) => threePageChunk(i)));
    expect(pagesReady(progress)).toBe(10);
    expect(pagesRemaining(progress)).toBe(0);
  });

  it("never reports more pages left than exist", () => {
    const progress = accumulate([page(1), page(2)]);
    expect(pagesRemaining(progress)!).toBeLessThanOrEqual(progress.totalPages!);
    expect(pagesRemaining(progress)).toBeGreaterThanOrEqual(0);
  });

  // A cache hit arrives as one synthetic chunk covering the whole document.
  it("handles the cache-hit shape: one chunk, every page", () => {
    const progress = accumulate([
      { chunk_index: 0, total_chunks: 1, pages_in_chunk: [1, 12], total_pages: 12 },
    ]);
    expect(pagesReady(progress)).toBe(12);
    expect(pagesRemaining(progress)).toBe(0);
  });

  // A sidecar older than `total_pages` must not black out the counter; the
  // UI drops the denominator instead of inventing one.
  it("leaves totalPages null when the sidecar doesn't send it", () => {
    const progress = accumulate([
      { chunk_index: 4, total_chunks: 50, pages_in_chunk: [5, 5] },
    ]);
    expect(progress.totalPages).toBeNull();
    expect(pagesReady(progress)).toBe(1);
    expect(pagesRemaining(progress)).toBeNull();
  });

  // #33: pages 101-150 of a 500-page book. "3 of 500 pages" would read as
  // barely started when a sixteenth of the job is done.
  it("counts toward the selected pages, not the document", () => {
    const progress = accumulate(
      [101, 102, 103].map((n) => ({
        ...page(n, 500),
        chunk_index: n - 101,
        total_chunks: 50,
        pages_to_translate: 50,
      })),
    );
    expect(progress.totalPages).toBe(50);
    expect(pagesReady(progress)).toBe(3);
    expect(pagesRemaining(progress)).toBe(47);
  });

  // A partial run served from the cache: the span says 120 because every page
  // repaints, `pages_to_translate` says 2 because that is what was asked for.
  // Counting the span against that denominator rendered "120 of 2 pages
  // translated".
  it("counts a partial cache hit as the pages it translated", () => {
    const progress = accumulate([cacheHit(2)]);
    expect(pagesReady(progress)).toBe(2);
    expect(progress.totalPages).toBe(2);
    expect(pagesRemaining(progress)).toBe(0);
  });

  it("leaves a whole-document cache hit counting every page", () => {
    const progress = accumulate([cacheHit(120)]);
    expect(pagesReady(progress)).toBe(120);
    expect(progress.totalPages).toBe(120);
    expect(pagesRemaining(progress)).toBe(0);
  });

  // A sidecar predating `pages_to_translate` still sends `cache_hit`. With
  // nothing better to read, the span is the only answer available.
  it("falls back to the span when a cache hit names no page count", () => {
    const progress = accumulate([
      { chunk_index: 0, total_chunks: 1, pages_in_chunk: [1, 120], cache_hit: true },
    ]);
    expect(pagesReady(progress)).toBe(120);
  });

  it("keeps a totalPages it already learned", () => {
    const progress = accumulate([
      page(1),
      { chunk_index: 1, total_chunks: 50, pages_in_chunk: [2, 2] },
    ]);
    expect(progress.totalPages).toBe(50);
  });
});

describe("describeChunk", () => {
  it("names the single page that finished", () => {
    expect(describeChunk(page(30))).toBe("Page 30 translated");
  });

  it("names the span a multi-page chunk covers", () => {
    expect(describeChunk(threePageChunk(1))).toBe("Pages 4–6 translated");
  });

  // The old copy read "Pages 1–30 translated" off the chunk's last page,
  // claiming 29 pages that hadn't been started.
  it("never claims pages before the chunk's own range", () => {
    expect(describeChunk(page(30))).not.toContain("1–");
  });
});

describe("pluralizePages", () => {
  it("keeps the noun singular for one page", () => {
    expect(pluralizePages(1)).toBe("1 page");
  });

  it("pluralizes everything else, zero included", () => {
    expect(pluralizePages(0)).toBe("0 pages");
    expect(pluralizePages(50)).toBe("50 pages");
  });
});
