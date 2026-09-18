/**
 * Accumulating `chunk_ready` events into "how much of this PDF is translated".
 *
 * The subtlety this module exists for: **chunks do not complete in page
 * order.** `_process_with_babeldoc` schedules them by distance from the page
 * the user is looking at (`pick_next` / the `_priority_anchor`), and up to
 * `max_parallel` run at once. Start a translation while viewing page 30 of 50
 * and the first event to land carries `chunk_index: 29`.
 *
 * The old code read `pages_in_chunk[1]` as "pages done so far", which is only
 * true if chunks arrive 1..N. On that same run it claimed "30 of 50 pages
 * translated · Pages 1–30 translated" after a single page had finished (#15).
 *
 * Two more things it gets right, both invisible until they bite:
 *
 * - **A chunk is not always a page.** Every backend runs 1-page chunks today,
 *   but the processor's chunk size is a knob (`_effective_pages_per_chunk`),
 *   so `total_chunks` is not a page count. Pages come from the event's own
 *   `pages_in_chunk` span, and the total from `pages_to_translate` — which is
 *   fewer than the document's `total_pages` when only some pages were asked
 *   for (#33).
 * - **A cache hit's span is not what it translated.** The one synthetic
 *   `chunk_ready` a cache hit emits claims the whole document, because the
 *   viewer is swapping in a different file and every page has to repaint.
 *   Read as a page count on a partial entry, that says 120 pages finished out
 *   of the 2 the run asked for. `pagesIn` takes `pages_to_translate` there
 *   instead.
 * - **The same chunk can arrive twice** (an SSE re-attach replays it), so
 *   completions are keyed by index rather than counted.
 */

/** The `chunk_ready` payload, as `ChunkReadyEvent.to_dict` serialises it. */
export interface ChunkReadyLike {
  chunk_index: number;
  total_chunks: number;
  /** `[first, last]`, 1-indexed and inclusive. */
  pages_in_chunk: [number, number];
  /** Pages in the whole document. `null`/absent on a sidecar that predates
   *  this field — the UI then reports progress without a denominator rather
   *  than inventing one. */
  total_pages?: number | null;
  /** Pages this run translates: `total_pages`, or fewer for a page
   *  selection. Absent on a sidecar that predates page selections. */
  pages_to_translate?: number | null;
  /** This event is the synthetic one a PDF-cache hit emits, not a chunk the
   *  pipeline finished. The accumulator has to know because such an event's
   *  `pages_in_chunk` is a repaint hint rather than a span of work. */
  cache_hit?: boolean;
}

export interface ChunkProgress {
  /** chunk index → pages that chunk covers. */
  pagesByChunk: Record<number, number>;
  totalChunks: number;
  /** Pages this run translates — the denominator of "N of M". */
  totalPages: number | null;
}

function pagesIn(event: ChunkReadyLike): number {
  // A cache hit's span is the whole document on purpose — the viewer is
  // swapping in a different file, so every page repaints — and that is not
  // what the run translated. The two agree for a whole-document entry and
  // differ for a partial one, where the span is 120 and the run is 2.
  if (event.cache_hit && event.pages_to_translate != null) {
    return event.pages_to_translate;
  }
  const [first, last] = event.pages_in_chunk;
  return Math.max(1, last - first + 1);
}

export function applyChunkReady(
  previous: ChunkProgress | null,
  event: ChunkReadyLike,
): ChunkProgress {
  return {
    pagesByChunk: {
      ...(previous?.pagesByChunk ?? {}),
      [event.chunk_index]: pagesIn(event),
    },
    totalChunks: event.total_chunks,
    totalPages:
      event.pages_to_translate ??
      event.total_pages ??
      previous?.totalPages ??
      null,
  };
}

export function chunksReady(progress: ChunkProgress): number {
  return Object.keys(progress.pagesByChunk).length;
}

export function pagesReady(progress: ChunkProgress): number {
  return Object.values(progress.pagesByChunk).reduce((a, b) => a + b, 0);
}

/** Pages still to come, or `null` when the sidecar didn't say how many there are. */
export function pagesRemaining(progress: ChunkProgress): number | null {
  if (progress.totalPages == null) return null;
  return Math.max(0, progress.totalPages - pagesReady(progress));
}

/** "1 page" / "3 pages". The overlay and the chunk messages all need the
 *  count and its noun together; spelling the branch out at each call site is
 *  how one of them ends up saying "1 pages". */
export function pluralizePages(count: number): string {
  return `${count} page${count === 1 ? "" : "s"}`;
}

/**
 * What just finished — the chunk's own pages, not a running range. Saying
 * "Pages 1–30" for the chunk covering page 30 claims 29 pages that haven't
 * been touched yet.
 */
export function describeChunk(event: ChunkReadyLike): string {
  const [first, last] = event.pages_in_chunk;
  return first === last
    ? `Page ${first} translated`
    : `Pages ${first}–${last} translated`;
}
