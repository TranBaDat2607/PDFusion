/**
 * Which recently-released pages keep their pdf.js resources, and which are
 * handed back (#76).
 *
 * `PageRenderer` frees a page's canvas the moment it leaves the render window,
 * but `PDFPageProxy.cleanup()` is a separate, far more expensive thing to undo:
 * it clears the proxy's `objs`, which is where the page's *decoded* images
 * live. pdf.js's worker-side `GlobalImageCache` does not cover the loss —
 * `shouldCache` keys on the set of page indices an image ref was seen on and
 * refuses anything under two, so art that appears on exactly one page (a slide
 * deck, a figure-heavy paper) is never cached. Cleaning up on release therefore
 * makes scrolling back one page re-pay the whole decode: measured on the deck
 * from #73/#74, a page carrying two ~2.2 MP JPEG 2000 images took 384–753 ms to
 * repaint on the way back, against 16–36 ms once the proxy is left alone. A
 * vector-only page costs ~7 ms either way, which is why this stayed invisible.
 *
 * So the cleanup is deferred by a page or two of scrolling instead. This module
 * owns that decision; `page-renderer.ts` only executes it.
 */

import type { PageRange } from "./layout";

/**
 * How many just-released pages keep their decoded images.
 *
 * A decoded 2.2 MP image is ~9 MB and the pathological pages carry two, so six
 * retained pages is roughly 110 MB on that document and nothing at all on one
 * whose pages are vector art. The bound is a *count*, so retention never grows
 * with the document — the reason this is not simply a larger `RENDER_RADIUS`,
 * which would multiply canvases as well, and a canvas is the larger allocation.
 *
 * Six covers the motions that re-read a page: page-up/page-down, and scrolling
 * back over a figure. Travel further than that and the decode is re-paid, which
 * is the pre-existing behaviour and only ever slower, never wrong.
 */
export const DECODE_RETAIN = 6;

/** `page` has just been released. Returns the pages whose decoded images are
 *  still worth keeping, least recently released first, and the ones that fell
 *  out of that list and should now be cleaned up. */
export function retainReleased(
  retained: readonly number[],
  page: number,
  limit: number = DECODE_RETAIN,
): { retained: number[]; evicted: number[] } {
  const next = retained.filter((candidate) => candidate !== page);
  next.push(page);
  const evicted = next.splice(0, Math.max(0, next.length - Math.max(0, limit)));
  return { retained: next, evicted };
}

/** Pages back inside `range` are wanted again, so they leave the list rather
 *  than sitting in it waiting to be cleaned up behind the reader's back. A
 *  `null` range means there is nothing on screen yet, which is not a reason to
 *  forget anything. */
export function forgetRetained(
  retained: readonly number[],
  range: PageRange | null,
): number[] {
  if (!range) return [...retained];
  return retained.filter((page) => page < range.first || page > range.last);
}
