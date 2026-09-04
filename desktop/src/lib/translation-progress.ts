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
 * - **A chunk is not always a page.** Argos uses 3-page chunks
 *   (`_PAGES_PER_CHUNK_ARGOS`) to amortise BabelDOC's layout-model reload, so
 *   `total_chunks` is not a page count. Pages come from the event's own
 *   `pages_in_chunk` span and the total from `total_pages`.
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
}

export interface ChunkProgress {
  /** chunk index → pages that chunk covers. */
  pagesByChunk: Record<number, number>;
  totalChunks: number;
  totalPages: number | null;
}

function pagesIn(event: ChunkReadyLike): number {
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
    totalPages: event.total_pages ?? previous?.totalPages ?? null,
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
