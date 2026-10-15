/**
 * Which pages of the translated pane may be drawn while a translation runs,
 * and how a newly translated page is revealed.
 *
 * Every rolling PDF is the whole document — translated pages for the chunks
 * finished so far, the original's pages everywhere else — so the pane can't
 * just draw what the file holds: the reader would see the source text first
 * and the translation replace it later. Instead the pane draws only the pages
 * a version has translated and leaves the rest blank white.
 *
 * The record is per version, not per run, because loading a version takes
 * time. When chunk N lands, the pane is still showing version N-1 until N has
 * loaded off-screen; drawing chunk N's page from N-1 would paint the original
 * onto it, the very flash this exists to prevent. Every version is kept: each
 * new path cancels the load in flight, so a pane can sit on an old version for
 * many chunks, and forgetting it would blank pages already shown translated. A
 * run is at most 100 pages, so that is at most a few thousand numbers.
 */

export interface TranslationMask {
  /** One entry per rolling artifact adopted this run, oldest first. */
  versions: readonly MaskVersion[];
  /** Set when the run has ended with this file as its result. */
  final?: string;
}

export interface MaskVersion {
  path: string;
  /** Pages translated in the file at `path`, ascending, no duplicates. */
  pages: readonly number[];
}

export function emptyMask(): TranslationMask {
  return { versions: [] };
}

/**
 * Record the artifact at `path`, which translated `pages` (`[first, last]`,
 * the shape of `chunk_ready.pages_in_chunk`) on top of everything the newest
 * version had. `null` adds no pages: a final or cancelled artifact repeats
 * what is already known.
 */
export function maskAfterArtifact(
  mask: TranslationMask,
  path: string,
  pages: readonly [number, number] | null,
): TranslationMask {
  const newest = mask.versions[mask.versions.length - 1] as MaskVersion | undefined;
  const translated = new Set(newest?.pages ?? []);
  if (pages) {
    const first = Math.min(pages[0], pages[1]);
    const last = Math.max(pages[0], pages[1]);
    for (let page = first; page <= last; page++) translated.add(page);
  }
  const version = {
    path,
    pages: [...translated].sort((a, b) => a - b),
  };
  const kept =
    newest?.path === path ? mask.versions.slice(0, -1) : mask.versions;
  return { versions: [...kept, version] };
}

/** What one pane may draw while it shows one file. */
export interface PaneMask {
  /** The pages that may be drawn, or `null` for every page. */
  drawable: ReadonlySet<number> | null;
  /** The pages translated in the file on screen. */
  translated: ReadonlySet<number>;
}

/**
 * The run is over and `finalPath` is its result. The mask lifts only for that
 * file: the backend's terminal event lands straight after the last chunk, often
 * before the pane has loaded it, and lifting for the file still on screen would
 * draw its untranslated pages. With no result there is nothing to wait for.
 */
export function finishMask(
  mask: TranslationMask | null,
  finalPath: string | null,
): TranslationMask | null {
  if (mask === null || finalPath === null) return null;
  return { ...mask, final: finalPath };
}

/** What the pane may draw while it shows the file at `path`: the pages that
 *  file has translated, or every page once it is the run's result. A file the
 *  run hasn't described draws nothing, which is the safe answer. */
export function paneMask(
  mask: TranslationMask | null,
  path: string | null,
): PaneMask | null {
  if (mask === null) return null;
  const version = mask.versions.find((candidate) => candidate.path === path);
  const translated = new Set(version?.pages ?? []);
  const isResult = mask.final !== undefined && mask.final === path;
  return { drawable: isResult ? null : translated, translated };
}

/**
 * What the renderer does when the mask changes. Only a page whose translation
 * arrived streams in. When the mask lifts on the result, the pages it adds that
 * were never translated (outside a page selection) are the original, and
 * sweeping them in would announce a translation that never happened.
 *
 * `forget` drops the pages still waiting to stream whenever either side is
 * unmasked, which is a run starting or ending. A page queued but not yet drawn
 * belongs to its run: carried into the next one it would sweep in as a
 * translation that run never made, and kept after the result shows it would
 * sweep in whenever the reader first scrolls to it, minutes later. `forget`
 * alone doesn't prevent the second: the `stream` returned alongside it when
 * the result shows is added after the drop, and `pendingAfterWindowDrawn`
 * retires whatever of it the first drawn window didn't use.
 */
export function maskTransition(
  previous: PaneMask | null,
  next: PaneMask | null,
  rendered: Iterable<number>,
): { hide: number[]; stream: number[]; forget: boolean } {
  const ascending = (a: number, b: number) => a - b;
  const drawable = next?.drawable ?? null;
  const hide =
    drawable === null
      ? []
      : [...rendered].filter((page) => !drawable.has(page)).sort(ascending);
  const stream =
    previous === null || next === null
      ? []
      : [...next.translated]
          .filter((page) => !previous.translated.has(page))
          .sort(ascending);
  const forget =
    previous === null ||
    previous.drawable === null ||
    next === null ||
    next.drawable === null;
  return { hide, stream, forget };
}

/** The pages still waiting to stream once `transition` (from
 *  `maskTransition`) has been applied to the ones that were. */
export function pendingAfterMask(
  pending: ReadonlySet<number>,
  transition: { stream: readonly number[]; forget: boolean },
): Set<number> {
  const next = new Set(transition.forget ? [] : pending);
  for (const page of transition.stream) next.add(page);
  return next;
}

/**
 * The pages still waiting to stream once the renderer has drawn everything it
 * can of `window`, its render window, under `mask`.
 *
 * Once the run's result is on screen, the pages that arrive with it are the
 * ones it draws straight away; anything still waiting after that would sweep
 * in whenever the reader first scrolls to it, for the rest of the session. The
 * transition that lifts the mask can't drop them itself: it is the same step
 * that adds the result's pages, which on a cache hit is every page, and when
 * the result is the first file the pane loads there is no window yet to tell
 * which of them are on screen. So they go here, after the first window of the
 * result has been drawn, and an empty window drops nothing.
 */
export function pendingAfterWindowDrawn(
  pending: ReadonlySet<number>,
  mask: PaneMask | null,
  window: readonly number[],
): Set<number> {
  const runOver = mask === null || mask.drawable === null;
  return runOver && window.length > 0 ? new Set() : new Set(pending);
}

/** Bands the reveal sweeps, top to bottom: a paragraph or so each on a letter
 *  page, so the page reads as arriving line by line. */
export const STREAM_LINES = 8;
/** The whole page is in by then, however many pages sweep at once (they run
 *  in parallel). Long enough for each band to get about thirteen frames at
 *  60 Hz; the first cut gave each band five, run back to back, and it read as
 *  a stepped wipe rather than a sweep. */
export const STREAM_MS = 720;
/** How many bands are moving at once. Each starts while the ones above it are
 *  still under way, so the eye follows one diagonal front down the page
 *  instead of a band stopping and the next one starting. */
const BANDS_IN_FLIGHT = 3;

/**
 * When band `index` (0 = top) of `lines` starts sweeping and for how long, in
 * ms. Every band takes the same time, so the bottom one ends last, at
 * `STREAM_MS`.
 */
export function streamBand(
  index: number,
  lines: number,
): { delay: number; duration: number } {
  const stagger = STREAM_MS / (lines - 1 + BANDS_IN_FLIGHT);
  return { delay: index * stagger, duration: BANDS_IN_FLIGHT * stagger };
}
