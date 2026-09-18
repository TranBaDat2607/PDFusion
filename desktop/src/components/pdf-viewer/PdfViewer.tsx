import {
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
  type Ref,
} from "react";

import { FindBar } from "@/components/pdf-viewer/FindBar";
import { PageRenderer } from "@/components/pdf-viewer/page-renderer";
import { ViewerToolbar } from "@/components/pdf-viewer/ViewerToolbar";
import "@/components/pdf-viewer/pdf-viewer.css";
import { usePdfDocument } from "@/hooks/usePdfDocument";
import { usePdfFind } from "@/hooks/usePdfFind";
import type { ArtifactChange } from "@/lib/pdf-viewer/artifact-swap";
import {
  CONTENT_PADDING,
  PAGE_GAP,
  fitWidthZoom,
  mostVisiblePage,
  offsetForAnchor,
  offsetForPage,
  pageGeometry,
  renderWindow,
  scrollAnchor,
  slotSize,
  visibleRange,
  zoomIn,
  zoomOut,
  type PageSize,
  type ScrollAnchor,
} from "@/lib/pdf-viewer/layout";

/** What the workspace's keyboard shortcuts can ask of a pane. */
export interface PdfViewerHandle {
  /** Shortcuts go to the other pane when this one has nothing loaded. */
  hasDocument: () => boolean;
  zoomIn: () => void;
  zoomOut: () => void;
  resetZoom: () => void;
  openFind: () => void;
  findNext: () => void;
  findPrevious: () => void;
}

interface PdfViewerProps {
  /** Absolute host filesystem path. The sidecar streams the bytes. */
  filePath: string | null;
  /** Optional placeholder when no file is loaded. */
  emptyState?: ReactNode;
  /** Page to scroll to (1-indexed). Updates from chat reference clicks. */
  scrollToPage?: number;
  /** Compact label shown in the bottom toolbar (e.g. "Original" / "Translated"). */
  label?: string;
  /** When set and `filePath` is null, render a single blank white page of
   *  these dimensions (CSS points at scale=1) instead of `emptyState`. */
  placeholderSize?: PageSize | null;
  /** Fired after a different document loads, with its first page's natural
   *  size (CSS points at scale=1). Fires again with `null` when the document
   *  is unloaded. */
  onFirstPageSize?: (size: PageSize | null) => void;
  /** Fired alongside `onFirstPageSize`, with the document's page count. */
  onPageCount?: (count: number | null) => void;
  /** Fired when the page the reader is on changes. Used by the original viewer
   *  to feed the translation priority scheduler so the page the user is
   *  looking at translates first. */
  onVisiblePageChange?: (page: number) => void;
  /** Bumped by the caller whenever the file at `filePath` may have been
   *  overwritten in place (e.g. Re-translate writing to the same rolling
   *  output path that's currently displayed). Forces a refetch of the path. */
  reloadKey?: number;
  /** Treat a new `filePath` as a new version of the document on screen, not a
   *  different document: load it off-screen, keep the reader's place, and
   *  repaint only the pages `changeLog` says changed. For the translated
   *  pane, whose rolling PDF is replaced after every chunk. */
  incrementalUpdates?: boolean;
  /** `translatedChanges` from the store. Only read with `incrementalUpdates`. */
  changeLog?: readonly ArtifactChange[];
  /** This pane receives the zoom and find shortcuts; its label says so. */
  active?: boolean;
  ref?: Ref<PdfViewerHandle>;
}

const NO_CHANGES: readonly ArtifactChange[] = [];

function* pageNumbers(first: number, last: number): Generator<number> {
  for (let page = first; page <= last; page++) yield page;
}

/**
 * A continuous-scroll PDF pane.
 *
 * The React side owns the layout: one slot per page, each sized from the
 * layout model, so the scroll height is right before any page renders and
 * stays right as canvases come and go. `PageRenderer` owns what goes inside the
 * slots. `usePdfDocument` owns the pdf.js document, and `usePdfFind` owns
 * search. See `lib/pdf-viewer/` for the pure half of all four.
 */
export function PdfViewer({
  filePath,
  emptyState,
  scrollToPage,
  label,
  placeholderSize,
  onFirstPageSize,
  onPageCount,
  onVisiblePageChange,
  reloadKey = 0,
  incrementalUpdates = false,
  changeLog = NO_CHANGES,
  active = false,
  ref,
}: PdfViewerProps) {
  const { loaded, loading, error } = usePdfDocument({
    filePath,
    reloadKey,
    incrementalUpdates,
    changeLog,
  });
  const doc = loaded?.doc ?? null;

  // Created once. StrictMode's development remount releases it rather than
  // destroying it, so the same instance keeps working afterwards.
  const [renderer] = useState(() => new PageRenderer());
  const [sizes, setSizes] = useState<PageSize[]>([]);
  const [zoom, setZoom] = useState(1);
  const [currentPage, setCurrentPage] = useState(1);
  const scrollerRef = useRef<HTMLDivElement>(null);

  const geometry = useMemo(() => pageGeometry(sizes, zoom), [sizes, zoom]);
  const pageCount =
    doc !== null && sizes.length === doc.numPages ? sizes.length : 0;

  // Scroll handlers and async work need the latest values without being
  // re-created for every change.
  const geometryRef = useRef(geometry);
  geometryRef.current = geometry;
  const sizesRef = useRef(sizes);
  sizesRef.current = sizes;
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;
  const currentPageRef = useRef(currentPage);
  currentPageRef.current = currentPage;
  const onVisiblePageChangeRef = useRef(onVisiblePageChange);
  onVisiblePageChangeRef.current = onVisiblePageChange;
  const onFirstPageSizeRef = useRef(onFirstPageSize);
  onFirstPageSizeRef.current = onFirstPageSize;
  const onPageCountRef = useRef(onPageCount);
  onPageCountRef.current = onPageCount;
  const reportedPageRef = useRef(0);

  /** Work out what's on screen from the scroll position: which pages the
   *  renderer keeps, and which page the reader is on. */
  const updateViewport = useCallback(() => {
    const scroller = scrollerRef.current;
    const geo = geometryRef.current;
    if (!scroller || geo.tops.length === 0) {
      renderer.update(null, null);
      return;
    }
    const { scrollTop, clientHeight } = scroller;
    const visible = visibleRange(geo, scrollTop, clientHeight);
    renderer.update(visible, visible && renderWindow(visible, geo.tops.length));
    const page = mostVisiblePage(geo, scrollTop, clientHeight);
    if (page === null) return;
    setCurrentPage(page);
    if (page !== reportedPageRef.current) {
      reportedPageRef.current = page;
      onVisiblePageChangeRef.current?.(page);
    }
  }, [renderer]);

  const frameRef = useRef<number | null>(null);
  const scheduleViewportUpdate = useCallback(() => {
    if (frameRef.current !== null) return;
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null;
      updateViewport();
    });
  }, [updateViewport]);

  useEffect(
    () => () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
      renderer.releaseAll();
    },
    [renderer],
  );

  // Hand each document to the renderer. A different document starts at the
  // top; a new version of the same one leaves the reader where they are.
  useEffect(() => {
    renderer.setDocument(doc, loaded?.changes ?? null);
    if (loaded && !loaded.keepPosition) {
      reportedPageRef.current = 0;
      const scroller = scrollerRef.current;
      if (scroller) {
        scroller.scrollTop = 0;
        scroller.scrollLeft = 0;
      }
    }
    scheduleViewportUpdate();
  }, [loaded, doc, renderer, scheduleViewportUpdate]);

  // Page sizes. Every page starts at the first page's size, so the scroll
  // height is about right immediately, and is corrected as the real sizes come
  // in. Chromium's scroll anchoring keeps the reader in place when a page above
  // them changes height. A new version of the same document keeps the sizes it
  // has and re-checks only the pages that changed.
  useEffect(() => {
    if (!loaded) {
      setSizes([]);
      onFirstPageSizeRef.current?.(null);
      onPageCountRef.current?.(null);
      return;
    }
    const { doc: current, changes, keepPosition } = loaded;
    const known = sizesRef.current;
    const reuse =
      keepPosition && changes !== null && known.length === current.numPages;
    let cancelled = false;

    void (async () => {
      let resolved: PageSize[];
      let pending: Iterable<number>;
      if (reuse) {
        resolved = known.slice();
        pending = changes === "all" ? pageNumbers(1, current.numPages) : changes;
      } else {
        const first = (await current.getPage(1)).getViewport({ scale: 1 });
        if (cancelled) return;
        const size = { width: first.width, height: first.height };
        if (!keepPosition) {
          onFirstPageSizeRef.current?.(size);
          onPageCountRef.current?.(current.numPages);
        }
        resolved = new Array<PageSize>(current.numPages).fill(size);
        setSizes(resolved.slice());
        pending = pageNumbers(2, current.numPages);
      }
      let corrected = 0;
      for (const page of pending) {
        const viewport = (await current.getPage(page)).getViewport({ scale: 1 });
        if (cancelled) return;
        const size = resolved[page - 1];
        if (size.width !== viewport.width || size.height !== viewport.height) {
          resolved[page - 1] = { width: viewport.width, height: viewport.height };
          corrected++;
          if (corrected % 50 === 0) setSizes(resolved.slice());
        }
      }
      if (corrected % 50 !== 0) setSizes(resolved.slice());
    })().catch(() => {
      // A destroyed document rejects; its successor resolves its own sizes.
    });

    return () => {
      cancelled = true;
    };
  }, [loaded]);

  // Where the reader was when a zoom was asked for. Restored once the new
  // layout exists, before paint, so the page doesn't visibly jump.
  const zoomAnchorRef = useRef<{
    anchor: ScrollAnchor | null;
    centerX: number;
  } | null>(null);

  const changeZoom = useCallback((compute: (zoom: number) => number) => {
    const target = compute(zoomRef.current);
    if (target === zoomRef.current) return;
    const scroller = scrollerRef.current;
    // Several steps before one render share the anchor of the first.
    if (scroller && !zoomAnchorRef.current) {
      zoomAnchorRef.current = {
        anchor: scrollAnchor(geometryRef.current, scroller.scrollTop),
        centerX:
          (scroller.scrollLeft + scroller.clientWidth / 2) /
          Math.max(1, scroller.scrollWidth),
      };
    }
    zoomRef.current = target;
    setZoom(target);
  }, []);

  useLayoutEffect(() => {
    const pending = zoomAnchorRef.current;
    zoomAnchorRef.current = null;
    const scroller = scrollerRef.current;
    if (pending && scroller) {
      if (pending.anchor) {
        scroller.scrollTop = offsetForAnchor(geometryRef.current, pending.anchor);
      }
      scroller.scrollLeft =
        pending.centerX * scroller.scrollWidth - scroller.clientWidth / 2;
    }
    renderer.setZoom(zoom);
    updateViewport();
  }, [zoom, renderer, updateViewport]);

  useLayoutEffect(() => {
    scheduleViewportUpdate();
  }, [geometry, scheduleViewportUpdate]);

  useEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller) return;
    const observer = new ResizeObserver(() => scheduleViewportUpdate());
    observer.observe(scroller);
    return () => observer.disconnect();
  }, [scheduleViewportUpdate]);

  const goToPage = useCallback(
    (page: number, behavior: ScrollBehavior = "auto") => {
      const scroller = scrollerRef.current;
      if (!scroller) return;
      scroller.scrollTo({
        top: offsetForPage(geometryRef.current, page),
        behavior,
      });
      updateViewport();
    },
    [updateViewport],
  );

  // Chat reference clicks. Only a new request scrolls; a document finishing
  // loading doesn't replay an old one.
  useEffect(() => {
    if (scrollToPage && pageCount > 0) {
      goToPage(Math.min(scrollToPage, pageCount), "smooth");
    }
  }, [scrollToPage]);

  const revealPage = useCallback(
    (page: number) => {
      const scroller = scrollerRef.current;
      if (!scroller) return;
      const visible = visibleRange(
        geometryRef.current,
        scroller.scrollTop,
        scroller.clientHeight,
      );
      if (visible && page >= visible.first && page <= visible.last) return;
      scroller.scrollTop = offsetForPage(geometryRef.current, page);
      updateViewport();
    },
    [updateViewport],
  );

  // Scrolls only this pane. `element.scrollIntoView` would also scroll any
  // ancestor that can, and the workspace's overflow-hidden containers can.
  const revealElement = useCallback(
    (element: HTMLElement) => {
      const scroller = scrollerRef.current;
      if (!scroller) return;
      const target = element.getBoundingClientRect();
      const view = scroller.getBoundingClientRect();
      scroller.scrollTop +=
        target.top + target.height / 2 - (view.top + scroller.clientHeight / 2);
      if (
        target.left < view.left ||
        target.right > view.left + scroller.clientWidth
      ) {
        scroller.scrollLeft +=
          target.left + target.width / 2 - (view.left + scroller.clientWidth / 2);
      }
      updateViewport();
    },
    [updateViewport],
  );

  const find = usePdfFind({
    doc,
    changes: loaded?.changes ?? null,
    renderer,
    currentPage: () => currentPageRef.current,
    revealPage,
    revealElement,
    onClose: () => scrollerRef.current?.focus({ preventScroll: true }),
  });

  const fitWidth = useCallback(() => {
    const scroller = scrollerRef.current;
    const size =
      sizesRef.current[currentPageRef.current - 1] ?? sizesRef.current[0];
    if (!scroller || !size) return;
    changeZoom(() => fitWidthZoom(size.width, scroller.clientWidth));
  }, [changeZoom]);

  useImperativeHandle(
    ref,
    () => ({
      hasDocument: () => doc !== null,
      zoomIn: () => changeZoom(zoomIn),
      zoomOut: () => changeZoom(zoomOut),
      resetZoom: () => changeZoom(() => 1),
      openFind: find.openFind,
      findNext: find.next,
      findPrevious: find.previous,
    }),
    [doc, changeZoom, find.openFind, find.next, find.previous],
  );

  // One stable callback per page. A new callback every render would make React
  // detach and re-attach every slot, which the renderer takes as the page
  // leaving the screen.
  const hostRefs = useRef(
    new Map<number, (host: HTMLDivElement | null) => void>(),
  );
  const hostRef = (page: number) => {
    let callback = hostRefs.current.get(page);
    if (!callback) {
      callback = (host) => renderer.setHost(page, host);
      hostRefs.current.set(page, callback);
    }
    return callback;
  };

  return (
    <div className="flex h-full flex-col bg-muted/30">
      <div className="relative min-h-0 flex-1">
        <div
          ref={scrollerRef}
          tabIndex={0}
          onScroll={scheduleViewportUpdate}
          className="absolute inset-0 overflow-auto outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40"
        >
          {pageCount > 0 && (
            <div
              className="mx-auto flex w-fit flex-col"
              style={{ gap: PAGE_GAP, padding: CONTENT_PADDING }}
            >
              {sizes.map((size, index) => {
                const slot = slotSize(size, zoom);
                return (
                  <div
                    key={index}
                    className="pdf-page relative shrink-0 overflow-hidden rounded-sm bg-white shadow-sm ring-1 ring-border"
                    style={
                      {
                        width: slot.width,
                        height: slot.height,
                        "--scale-factor": zoom,
                      } as CSSProperties
                    }
                  >
                    <span
                      aria-hidden
                      className="absolute inset-0 flex select-none items-center justify-center text-xs text-neutral-400"
                    >
                      Page {index + 1}
                    </span>
                    <div ref={hostRef(index + 1)} className="absolute inset-0" />
                  </div>
                );
              })}
            </div>
          )}
          {!filePath && placeholderSize && (
            <div className="mx-auto w-fit" style={{ padding: CONTENT_PADDING }}>
              <div
                aria-label="Translation placeholder page"
                className="rounded-sm bg-white shadow-sm ring-1 ring-border"
                style={slotSize(placeholderSize, zoom)}
              />
            </div>
          )}
          {!filePath && !placeholderSize && emptyState && (
            <div className="flex h-full items-center justify-center">
              {emptyState}
            </div>
          )}
          {filePath && !doc && loading && (
            <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
              Loading PDF…
            </div>
          )}
          {filePath && !doc && error && (
            <div className="flex h-full items-center justify-center px-6 text-center text-sm text-destructive">
              {error}
            </div>
          )}
        </div>

        {find.open && doc && (
          <FindBar
            query={find.query}
            onQueryChange={find.setQuery}
            matchCount={find.matchCount}
            selected={find.selected}
            searching={find.searching}
            focusKey={find.focusKey}
            onNext={find.next}
            onPrevious={find.previous}
            onClose={find.close}
          />
        )}
      </div>

      {(doc || label) && (
        <ViewerToolbar
          label={label}
          active={active}
          hasDocument={pageCount > 0}
          pageCount={pageCount}
          currentPage={currentPage}
          onGoToPage={goToPage}
          zoom={zoom}
          onZoomIn={() => changeZoom(zoomIn)}
          onZoomOut={() => changeZoom(zoomOut)}
          onFitWidth={fitWidth}
          onFind={find.openFind}
        />
      )}
    </div>
  );
}
