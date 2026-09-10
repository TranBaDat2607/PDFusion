/**
 * The continuous-scroll PDF viewer's geometry, as plain numbers.
 *
 * At any moment most pages have no canvas: `PdfViewer` keeps only the visible
 * pages and `RENDER_RADIUS` either side of them rendered (#30). So a page's
 * position can never be read off what it renders. Every page slot is sized from
 * this model, and scroll handling reads the same model back.
 *
 * That is why the JSX and the maths both size a slot through `slotSize`. A
 * one-pixel rounding difference between the two, repeated over a few hundred
 * pages, is enough to land a go-to or a zoom on the wrong page.
 */

export interface PageSize {
  width: number;
  height: number;
}

/** 1-indexed and inclusive. */
export interface PageRange {
  first: number;
  last: number;
}

export interface PageGeometry {
  /** Each slot's top edge, measured from the top of the scrollable content. */
  tops: number[];
  heights: number[];
  /** Height of the whole scrollable content, padding included. */
  contentHeight: number;
}

/** A position in the document that survives a zoom: a page, and how far down
 *  it the viewport's top edge is (0 = its top edge, 1 = its bottom edge). */
export interface ScrollAnchor {
  page: number;
  fraction: number;
}

/** Vertical space between two page slots, in CSS px. */
export const PAGE_GAP = 16;
/** Padding around the column of pages, in CSS px. */
export const CONTENT_PADDING = 16;
/** Pages kept rendered on either side of the visible ones. Every page further
 *  away has its canvas released; its slot keeps its height. */
export const RENDER_RADIUS = 3;

export const ZOOM_MIN = 0.2;
export const ZOOM_MAX = 5;
const ZOOM_STEP = 1.2;

/**
 * The largest canvas backing store a page gets, in device pixels (4096²).
 *
 * The cost of a canvas is `width × height × 4` bytes, so at 500% on a 2× display
 * a single Letter page wants ~190 MB. Past this cap the canvas stays at the cap
 * and CSS stretches it: a page gets softer at extreme zoom rather than
 * exhausting memory. Same idea as pdf.js's own `maxCanvasPixels`.
 */
export const MAX_CANVAS_PIXELS = 2 ** 24;

export function slotSize(size: PageSize, zoom: number): PageSize {
  return {
    width: Math.floor(size.width * zoom),
    height: Math.floor(size.height * zoom),
  };
}

export function pageGeometry(
  sizes: readonly PageSize[],
  zoom: number,
): PageGeometry {
  const tops: number[] = [];
  const heights: number[] = [];
  let y = CONTENT_PADDING;
  for (const size of sizes) {
    const { height } = slotSize(size, zoom);
    tops.push(y);
    heights.push(height);
    y += height + PAGE_GAP;
  }
  const contentHeight =
    sizes.length > 0 ? y - PAGE_GAP + CONTENT_PADDING : 2 * CONTENT_PADDING;
  return { tops, heights, contentHeight };
}

/** 0-indexed: the first page whose bottom edge is below `y`, or the last page
 *  when `y` is past all of them. */
function pageEndingAfter(geo: PageGeometry, y: number): number {
  const n = geo.tops.length;
  let lo = 0;
  let hi = n - 1;
  let found = n - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (geo.tops[mid] + geo.heights[mid] > y) {
      found = mid;
      hi = mid - 1;
    } else {
      lo = mid + 1;
    }
  }
  return found;
}

/** 0-indexed: the last page whose top edge is above `y`, or the first page. */
function pageStartingBefore(geo: PageGeometry, y: number): number {
  let lo = 0;
  let hi = geo.tops.length - 1;
  let found = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (geo.tops[mid] < y) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found;
}

/** Pages that intersect the viewport, or `null` for a document with none. */
export function visibleRange(
  geo: PageGeometry,
  scrollTop: number,
  viewportHeight: number,
): PageRange | null {
  if (geo.tops.length === 0) return null;
  const first = pageEndingAfter(geo, scrollTop);
  const last = Math.max(
    first,
    pageStartingBefore(geo, scrollTop + viewportHeight),
  );
  return { first: first + 1, last: last + 1 };
}

/**
 * The page the reader is on: the one covering the most of the viewport, the
 * earlier page on a tie.
 *
 * The exception is the bottom of the document. Zoomed out, the last page never
 * covers more of the viewport than the page before it, so by overlap alone the
 * indicator could never read "50 / 50".
 */
export function mostVisiblePage(
  geo: PageGeometry,
  scrollTop: number,
  viewportHeight: number,
): number | null {
  const range = visibleRange(geo, scrollTop, viewportHeight);
  if (!range) return null;
  const viewportBottom = scrollTop + viewportHeight;
  if (viewportBottom >= geo.contentHeight - 1) return range.last;

  let best = range.first;
  let bestOverlap = -Infinity;
  for (let page = range.first; page <= range.last; page++) {
    const top = geo.tops[page - 1];
    const bottom = top + geo.heights[page - 1];
    const overlap =
      Math.min(bottom, viewportBottom) - Math.max(top, scrollTop);
    if (overlap > bestOverlap) {
      best = page;
      bestOverlap = overlap;
    }
  }
  return best;
}

/** The pages that should hold a canvas: the visible ones plus `radius` either
 *  side, clamped to the document. */
export function renderWindow(
  visible: PageRange,
  pageCount: number,
  radius: number = RENDER_RADIUS,
): PageRange {
  return {
    first: Math.max(1, visible.first - radius),
    last: Math.min(pageCount, visible.last + radius),
  };
}

/**
 * The order to render a window's pages in: the visible pages top to bottom,
 * then the buffer pages nearest first, alternating below and above.
 *
 * Rendering is sequential and this order is recomputed after every page, so the
 * reader's pages always come next, however fast they scroll.
 */
export function renderPriority(visible: PageRange, window: PageRange): number[] {
  const order: number[] = [];
  for (let page = visible.first; page <= visible.last; page++) order.push(page);
  for (let distance = 1; ; distance++) {
    const below = visible.last + distance;
    const above = visible.first - distance;
    const hasBelow = below <= window.last;
    const hasAbove = above >= window.first;
    if (!hasBelow && !hasAbove) break;
    if (hasBelow) order.push(below);
    if (hasAbove) order.push(above);
  }
  return order;
}

export function scrollAnchor(
  geo: PageGeometry,
  scrollTop: number,
): ScrollAnchor | null {
  if (geo.tops.length === 0) return null;
  const index = pageEndingAfter(geo, scrollTop);
  const height = geo.heights[index];
  return {
    page: index + 1,
    fraction: height > 0 ? (scrollTop - geo.tops[index]) / height : 0,
  };
}

/** The `scrollTop` that puts the viewport back at `anchor`, in a geometry that
 *  may since have been zoomed. */
export function offsetForAnchor(geo: PageGeometry, anchor: ScrollAnchor): number {
  if (geo.tops.length === 0) return 0;
  const index = clampIndex(anchor.page - 1, geo.tops.length);
  return Math.max(0, geo.tops[index] + anchor.fraction * geo.heights[index]);
}

/** The `scrollTop` that shows `page` at the top of the viewport, leaving the
 *  gap above it visible so the previous page's edge isn't. */
export function offsetForPage(geo: PageGeometry, page: number): number {
  if (geo.tops.length === 0) return 0;
  const index = clampIndex(page - 1, geo.tops.length);
  return Math.max(0, geo.tops[index] - PAGE_GAP);
}

function clampIndex(index: number, count: number): number {
  return Math.min(count - 1, Math.max(0, index));
}

/** Device pixels per PDF point for a page's canvas: `zoom × dpr`, lowered to
 *  whatever keeps the canvas within `maxPixels`. */
export function canvasScale(
  size: PageSize,
  zoom: number,
  dpr: number,
  maxPixels: number = MAX_CANVAS_PIXELS,
): number {
  const wanted = zoom * dpr;
  const area = size.width * size.height;
  if (area <= 0 || area * wanted * wanted <= maxPixels) return wanted;
  return Math.sqrt(maxPixels / area);
}

export function clampZoom(zoom: number): number {
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, zoom));
}

export function zoomIn(zoom: number): number {
  return clampZoom(zoom * ZOOM_STEP);
}

export function zoomOut(zoom: number): number {
  return clampZoom(zoom / ZOOM_STEP);
}

/** The zoom at which a page `pageWidth` points wide fills a scroller
 *  `containerWidth` px wide, less the column's padding. */
export function fitWidthZoom(pageWidth: number, containerWidth: number): number {
  if (pageWidth <= 0 || containerWidth <= 0) return 1;
  return clampZoom((containerWidth - 2 * CONTENT_PADDING) / pageWidth);
}

/**
 * What the page box's text means as a page number: `null` when it isn't one,
 * and the last page when it is past the end. Asking for page 999 of 50 is
 * asking for "the end", and there is nothing more useful to do with it.
 */
export function parsePageInput(text: string, pageCount: number): number | null {
  const trimmed = text.trim();
  if (pageCount < 1 || !/^\d+$/.test(trimmed)) return null;
  const page = Number.parseInt(trimmed, 10);
  if (page < 1) return null;
  return Math.min(page, pageCount);
}
