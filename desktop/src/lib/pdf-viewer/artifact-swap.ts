/**
 * Which pages of a streaming translation need repainting when a new rolling
 * PDF arrives.
 *
 * Each `chunk_ready` names a new `{stem}_translated_v{N}.pdf`, identical to the
 * previous version except for the pages that chunk translated. The viewer loads
 * it off-screen, then repaints only those pages; every other canvas already
 * shows the right thing.
 *
 * Two things make "only those pages" harder than reading the latest event:
 *
 * - **A load can be overtaken.** Loading a version takes time, and when the next
 *   chunk lands meanwhile, the load is abandoned for the newer file. The newer
 *   file differs from what's on screen by *both* chunks, so changes accumulate
 *   (`accumulateChanges`) until a version actually reaches the screen.
 * - **React renders batches, not events.** Two `chunk_ready`s parsed from one
 *   network read are two store writes but one render. A viewer that read "the
 *   latest chunk's pages" from the store would never hear about the first. So
 *   the store appends every adopted artifact to a short log
 *   (`appendArtifactChange`), and the viewer folds in every entry newer than
 *   the last one it saw (`accumulateSince`).
 */

/** Pages known to differ from what's on screen, or `"all"` when a change's
 *  extent is unknown. */
export type PendingChanges = ReadonlySet<number> | "all";

/** One adopted artifact. `pages` is `[first, last]`, 1-indexed and inclusive,
 *  the shape of `chunk_ready.pages_in_chunk`; `null` when unknown. */
export interface ArtifactChange {
  seq: number;
  pages: readonly [number, number] | null;
}

/** Entries the log keeps. A viewer that falls further behind than this
 *  repaints everything, which is always correct, only slower. */
export const CHANGE_LOG_LIMIT = 64;

/**
 * Fold one more artifact change into `pending`. A `null` hint means "changed,
 * extent unknown": the final `done` artifact, a Re-translate, a cache hit.
 */
export function accumulateChanges(
  pending: PendingChanges | null,
  hint: readonly [number, number] | null | undefined,
): PendingChanges {
  if (pending === "all" || hint == null) return "all";
  const next = new Set(pending ?? []);
  const first = Math.min(hint[0], hint[1]);
  const last = Math.max(hint[0], hint[1]);
  for (let page = first; page <= last; page++) next.add(page);
  return next;
}

/** The rendered pages a swap has to repaint, in page order. Pages that aren't
 *  rendered need nothing: they render from the new document when they scroll
 *  into view. */
export function pagesToRefresh(
  rendered: Iterable<number>,
  pending: PendingChanges,
): number[] {
  const pages: number[] = [];
  for (const page of rendered) {
    if (pending === "all" || pending.has(page)) pages.push(page);
  }
  return pages.sort((a, b) => a - b);
}

export function latestSeq(log: readonly ArtifactChange[]): number {
  return log.length > 0 ? log[log.length - 1].seq : 0;
}

export function appendArtifactChange(
  log: readonly ArtifactChange[],
  pages: readonly [number, number] | null,
): ArtifactChange[] {
  const next = [...log, { seq: latestSeq(log) + 1, pages }];
  return next.length > CHANGE_LOG_LIMIT
    ? next.slice(next.length - CHANGE_LOG_LIMIT)
    : next;
}

/** Fold every log entry after `seen` into `pending`, and report the newest
 *  entry now accounted for. */
export function accumulateSince(
  pending: PendingChanges | null,
  log: readonly ArtifactChange[],
  seen: number,
): { pending: PendingChanges | null; seen: number } {
  const newest = latestSeq(log);
  if (newest <= seen) return { pending, seen };
  // Entries this viewer never saw were already trimmed off the front.
  if (log[0].seq > seen + 1) return { pending: "all", seen: newest };
  let next = pending;
  for (const entry of log) {
    if (entry.seq > seen) next = accumulateChanges(next, entry.pages);
  }
  return { pending: next, seen: newest };
}
