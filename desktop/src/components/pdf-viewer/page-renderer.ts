/**
 * Draws a PDF's pages into the viewer's page slots. It works imperatively
 * because a page's canvas and text layer are pdf.js output, not React state.
 *
 * It owns three things `PdfViewer` must not do itself (#30):
 *
 * - **Recycling.** Only pages in the render window (visible ± `RENDER_RADIUS`)
 *   hold a canvas. A page leaving the window is released immediately: its
 *   render is cancelled, its canvas's backing store zeroed, its text layer
 *   removed. Its pdf.js resources go later, once `DECODE_RETAIN` further pages
 *   have been released, so that scrolling back does not re-pay the decode
 *   (#76). Slots take their size from the layout model, never from a canvas, so
 *   a release moves nothing.
 * - **Ordering.** Pages render one at a time. The next page is picked after
 *   every render from the current priority list, so a fast scroll never leaves
 *   a backlog of renders for pages it has already passed.
 * - **No blank frames.** A page with a canvas keeps it until the replacement
 *   has finished rendering. After a zoom, the old bitmap stays up, stretched by
 *   CSS, until `ZOOM_SETTLE_MS` passes without another zoom. After a document
 *   swap, only the pages that changed are redrawn at all.
 *
 * Every `await` re-checks that the document, its generation and the page's
 * state are still the ones it started with. A render that lost a race is
 * dropped rather than landing on the wrong page or the wrong document.
 */

import { RenderingCancelledException, TextLayer } from "pdfjs-dist";
import type {
  PDFDocumentProxy,
  PDFPageProxy,
  PageViewport,
  RenderTask,
} from "pdfjs-dist";

import { attachSelectionHandling } from "@/components/pdf-viewer/text-selection";
import {
  pagesToRefresh,
  type PendingChanges,
} from "@/lib/pdf-viewer/artifact-swap";
import {
  renewRetained,
  retainReleased,
} from "@/lib/pdf-viewer/decode-retention";
import {
  canvasScale,
  renderPriority,
  type PageRange,
} from "@/lib/pdf-viewer/layout";

type TextContent = Awaited<ReturnType<PDFPageProxy["getTextContent"]>>;

/** How long zoom has to stay put before rendered pages are redrawn at it. */
const ZOOM_SETTLE_MS = 200;

export interface RenderedTextLayer {
  /** One span per text item that has a `str`, in order. */
  divs: HTMLElement[];
  strs: string[];
  /** The text content the layer was built from. */
  items: TextContent["items"];
}

export interface PageRendererEvents {
  /** A page's text layer was built (or rebuilt) and is in the DOM. */
  onTextLayer?: (page: number) => void;
  /** A page's canvas and text layer were removed. */
  onRelease?: (page: number) => void;
}

interface PageState {
  canvas: HTMLCanvasElement | null;
  /** Zoom the canvas was drawn at. */
  zoom: number;
  /** Changed in a document swap: redraw it even though it has a canvas. */
  dirty: boolean;
  task: RenderTask | null;
  textLayer: TextLayer | null;
  textDiv: HTMLDivElement | null;
  textItems: TextContent["items"] | null;
  textZoom: number;
  pendingText: TextLayer | null;
  detachSelection: (() => void) | null;
  /** `generation:zoom` a render failed at. Not retried until one of them
   *  changes, so a page that can't render doesn't spin the queue. */
  failedAt: string | null;
}

function newPageState(): PageState {
  return {
    canvas: null,
    zoom: 0,
    dirty: false,
    task: null,
    textLayer: null,
    textDiv: null,
    textItems: null,
    textZoom: 0,
    pendingText: null,
    detachSelection: null,
    failedAt: null,
  };
}

/** Release a canvas's backing store now rather than at garbage collection. A
 *  page bitmap is megabytes, and one fast scroll drops dozens of them. */
function freeCanvas(canvas: HTMLCanvasElement): void {
  canvas.width = 0;
  canvas.height = 0;
  canvas.remove();
}

export class PageRenderer {
  events: PageRendererEvents = {};

  private readonly hosts = new Map<number, HTMLElement>();
  private readonly pages = new Map<number, PageState>();
  private doc: PDFDocumentProxy | null = null;
  private generation = 0;
  private zoom = 1;
  private zoomSettlesAt = 0;
  private zoomTimer: ReturnType<typeof setTimeout> | undefined;
  private queue: number[] = [];
  private pumping = false;
  /** Released pages whose pdf.js resources are still held, oldest first. */
  private retained: number[] = [];

  /** Register the element page `page` draws into, or with `null`, forget it. */
  setHost(page: number, host: HTMLElement | null): void {
    if (host) {
      this.hosts.set(page, host);
      this.pump();
    } else {
      // The slot itself is gone, so there is nothing to scroll back to: a page
      // that was holding decoded images gives them up now rather than waiting
      // for a later release to push it off the list. It comes off the list
      // first, and is released without re-entering it: retaining a page we are
      // about to clean up anyway would evict one that is still worth keeping.
      // A page that never rendered is on no list and costs nothing here.
      const decoded = this.pages.has(page) || this.retained.includes(page);
      this.retained = this.retained.filter((candidate) => candidate !== page);
      this.release(page, false);
      if (decoded) this.cleanupPage(page, this.doc);
      this.hosts.delete(page);
    }
  }

  /**
   * Show `doc`. With `changes` null it's a different document, so every page
   * is dropped. Otherwise it's a new version of the document on screen, and
   * only the rendered pages in `changes` are redrawn.
   */
  setDocument(
    doc: PDFDocumentProxy | null,
    changes: PendingChanges | null,
  ): void {
    if (doc === this.doc) return;
    // Both of these clean up pages of the outgoing document, so they have to
    // run before `this.doc` is reassigned — and `flushRetained` has to be
    // handed that document rather than reading it back, because the cleanups
    // settle in a microtask, by which point it is the incoming one.
    if (!doc || changes === null) {
      for (const page of [...this.pages.keys()]) this.release(page);
    }
    this.flushRetained(this.doc);
    this.doc = doc;
    this.generation++;
    if (doc && changes !== null) {
      for (const page of pagesToRefresh(this.pages.keys(), changes)) {
        const state = this.pages.get(page);
        if (state) state.dirty = true;
      }
    }
    this.pump();
  }

  /** New zoom. Empty slots render at it straight away; pages that already
   *  have a canvas keep it until zooming stops. */
  setZoom(zoom: number): void {
    if (zoom === this.zoom) return;
    this.zoom = zoom;
    this.zoomSettlesAt = performance.now() + ZOOM_SETTLE_MS;
    clearTimeout(this.zoomTimer);
    this.zoomTimer = setTimeout(() => this.pump(), ZOOM_SETTLE_MS + 1);
    this.pump();
  }

  /** What's on screen. Pages outside `range` are released; pages inside are
   *  drawn, visible ones first. */
  update(visible: PageRange | null, range: PageRange | null): void {
    // Before the releases below, so a page scrolled back into the window is
    // already at the safe end of the retention list before this tick can start
    // evicting from the other one.
    this.retained = renewRetained(this.retained, range);
    for (const page of [...this.pages.keys()]) {
      if (!range || page < range.first || page > range.last) {
        this.release(page);
      }
    }
    this.queue = visible && range ? renderPriority(visible, range) : [];
    this.pump();
  }

  textLayer(page: number): RenderedTextLayer | null {
    const state = this.pages.get(page);
    if (!state?.textLayer || !state.textItems) return null;
    return {
      divs: state.textLayer.textDivs,
      strs: state.textLayer.textContentItemsStr,
      items: state.textItems,
    };
  }

  textLayerPages(): number[] {
    const pages: number[] = [];
    for (const [page, state] of this.pages) {
      if (state.textLayer) pages.push(page);
    }
    return pages;
  }

  /** Drop every canvas and the document. The renderer stays usable afterwards:
   *  React's StrictMode unmounts and remounts once in development. */
  releaseAll(): void {
    clearTimeout(this.zoomTimer);
    for (const page of [...this.pages.keys()]) this.release(page);
    this.flushRetained(this.doc);
    this.queue = [];
    this.doc = null;
    this.generation++;
  }

  /** With `retain` false the page's pdf.js resources are the caller's to deal
   *  with: it is not put on the retention list, and so evicts nothing. */
  private release(page: number, retain = true): void {
    const state = this.pages.get(page);
    if (!state) return;
    this.pages.delete(page);
    state.task?.cancel();
    state.pendingText?.cancel();
    state.detachSelection?.();
    if (state.canvas) freeCanvas(state.canvas);
    state.textDiv?.remove();
    this.events.onRelease?.(page);

    if (!retain) return;
    // The canvas goes now; the page's decoded images do not. Cleaning up here
    // would make scrolling back one page re-decode it from scratch, so the
    // cleanup waits until `DECODE_RETAIN` further pages have been released
    // (#76).
    const { retained, evicted } = retainReleased(this.retained, page);
    this.retained = retained;
    for (const stale of evicted) this.cleanupPage(stale, this.doc);
  }

  /** Hand a released page's operator list and decoded images back to pdf.js. It
   *  does nothing while a cancelled render is still settling — pdf.js retries
   *  once that render completes, so the page is freed either way. */
  private cleanupPage(page: number, doc: PDFDocumentProxy | null): void {
    // The proxy is resolved from `doc`, so this can only ever free a page of
    // the document the caller meant. `pages` is consulted only while that is
    // still the document on screen: after a swap it describes the incoming one
    // and says nothing about this page.
    void doc?.getPage(page).then(
      (proxy) => {
        if (this.doc !== doc || !this.pages.has(page)) proxy.cleanup();
      },
      () => undefined,
    );
  }

  private flushRetained(doc: PDFDocumentProxy | null): void {
    const pages = this.retained;
    this.retained = [];
    for (const page of pages) this.cleanupPage(page, doc);
  }

  private needsRender(page: number): boolean {
    const doc = this.doc;
    if (!doc || page > doc.numPages || !this.hosts.has(page)) return false;
    const state = this.pages.get(page);
    if (!state?.canvas) return state?.failedAt !== this.failureKey();
    if (state.failedAt === this.failureKey()) return false;
    if (state.dirty) return true;
    return state.zoom !== this.zoom && performance.now() >= this.zoomSettlesAt;
  }

  private failureKey(): string {
    return `${this.generation}:${this.zoom}`;
  }

  private pump(): void {
    if (this.pumping) return;
    this.pumping = true;
    void (async () => {
      try {
        for (;;) {
          const page = this.queue.find((candidate) =>
            this.needsRender(candidate),
          );
          if (page === undefined) break;
          await this.renderPage(page);
        }
      } finally {
        this.pumping = false;
      }
    })();
  }

  private async renderPage(pageNumber: number): Promise<void> {
    const doc = this.doc;
    const host = this.hosts.get(pageNumber);
    if (!doc || !host) return;
    const generation = this.generation;
    const zoom = this.zoom;
    let state = this.pages.get(pageNumber);
    if (!state) {
      state = newPageState();
      this.pages.set(pageNumber, state);
    }
    const page_ = state;
    const current = () =>
      this.doc === doc &&
      this.generation === generation &&
      this.pages.get(pageNumber) === page_ &&
      this.hosts.get(pageNumber) === host;
    const fail = (what: string, error: unknown) => {
      if (!current()) return;
      page_.failedAt = `${generation}:${zoom}`;
      console.warn(`Failed to ${what} page ${pageNumber}:`, error);
    };

    let page: PDFPageProxy;
    try {
      page = await doc.getPage(pageNumber);
    } catch (error) {
      fail("load", error);
      return;
    }
    if (!current()) return;

    const rebuildText = !page_.textDiv || page_.dirty;
    const base = page.getViewport({ scale: 1 });
    const scale = canvasScale(
      { width: base.width, height: base.height },
      zoom,
      window.devicePixelRatio || 1,
    );
    const viewport = page.getViewport({ scale });
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.floor(viewport.width));
    canvas.height = Math.max(1, Math.floor(viewport.height));
    canvas.className = "pdf-page-canvas";

    const task = page.render({ canvas, viewport });
    page_.task = task;
    try {
      await task.promise;
    } catch (error) {
      freeCanvas(canvas);
      if (page_.task === task) page_.task = null;
      if (!(error instanceof RenderingCancelledException)) fail("render", error);
      return;
    }
    if (page_.task === task) page_.task = null;
    if (!current()) {
      freeCanvas(canvas);
      return;
    }

    const previous = page_.canvas;
    if (previous) {
      previous.replaceWith(canvas);
      freeCanvas(previous);
    } else {
      // Fade in only onto an empty slot; a replacement swaps in place.
      canvas.style.opacity = "0";
      host.insertBefore(canvas, page_.textDiv);
      requestAnimationFrame(() => {
        canvas.style.opacity = "1";
      });
    }
    page_.canvas = canvas;
    page_.zoom = zoom;
    page_.dirty = false;
    page_.failedAt = null;

    const textViewport = page.getViewport({ scale: zoom });
    if (rebuildText) {
      await this.buildTextLayer(pageNumber, page_, page, textViewport, current);
    } else if (page_.textLayer && page_.textZoom !== zoom) {
      page_.textLayer.update({ viewport: textViewport });
      page_.textZoom = zoom;
    }
  }

  private async buildTextLayer(
    pageNumber: number,
    state: PageState,
    page: PDFPageProxy,
    viewport: PageViewport,
    current: () => boolean,
  ): Promise<void> {
    state.pendingText?.cancel();
    let content: TextContent;
    try {
      content = await page.getTextContent();
    } catch {
      // Selection and find are extras: the page itself is already on screen.
      return;
    }
    if (!current()) return;

    const div = document.createElement("div");
    div.className = "textLayer";
    const layer = new TextLayer({
      textContentSource: content,
      container: div,
      viewport,
    });
    state.pendingText = layer;
    try {
      await layer.render();
    } catch {
      if (state.pendingText === layer) state.pendingText = null;
      return;
    }
    if (state.pendingText === layer) state.pendingText = null;
    const host = this.hosts.get(pageNumber);
    if (!current() || !host) return;

    // Swap the new layer in only now, so a rebuild never leaves the page
    // unselectable while text content loads.
    state.detachSelection?.();
    state.textDiv?.remove();
    host.append(div);
    state.textLayer = layer;
    state.textDiv = div;
    state.textItems = content.items;
    state.textZoom = viewport.scale;
    state.detachSelection = attachSelectionHandling(div);
    this.events.onTextLayer?.(pageNumber);
  }
}
