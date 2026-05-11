import { useCallback, useEffect, useRef, useState } from "react";
import { Maximize2, Minus, Plus } from "lucide-react";
import * as pdfjs from "pdfjs-dist";
import workerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";

import { Button } from "@/components/ui/button";
import { sidecarToken, sidecarUrl } from "@/lib/api-client";

pdfjs.GlobalWorkerOptions.workerSrc = workerSrc;

type PdfDoc = pdfjs.PDFDocumentProxy;

interface PdfViewerProps {
  /** Absolute host filesystem path. The sidecar streams the bytes. */
  filePath: string | null;
  /** Optional placeholder when no file is loaded. */
  emptyState?: React.ReactNode;
  /** Page to scroll to (1-indexed). Updates from chat reference clicks. */
  scrollToPage?: number;
  /** Compact label shown in the bottom toolbar (e.g. "Original" / "Translated"). */
  label?: string;
  /** When set and `filePath` is null, render a single blank white page of
   *  these dimensions (CSS points at scale=1) instead of `emptyState`. */
  placeholderSize?: { width: number; height: number } | null;
  /** Fired once after a document loads, with the first page's natural size
   *  (CSS points at scale=1). Fires again with `null` when the document is
   *  unloaded. */
  onFirstPageSize?: (size: { width: number; height: number } | null) => void;
}

export function PdfViewer({
  filePath,
  emptyState,
  scrollToPage,
  label,
  placeholderSize,
  onFirstPageSize,
}: PdfViewerProps) {
  const [doc, setDoc] = useState<PdfDoc | null>(null);
  const [pageCount, setPageCount] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef<Array<HTMLDivElement | null>>([]);
  const renderedPages = useRef<Set<number>>(new Set());

  // Load the document whenever the file path changes
  useEffect(() => {
    if (!filePath) {
      setDoc(null);
      setPageCount(0);
      renderedPages.current.clear();
      onFirstPageSize?.(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);

    (async () => {
      try {
        const url = await sidecarUrl(
          `/pdf/file?path=${encodeURIComponent(filePath)}`,
        );
        const token = await sidecarToken();
        const task = pdfjs.getDocument({
          url,
          httpHeaders: { Authorization: `Bearer ${token}` },
        });
        const loaded = await task.promise;
        if (cancelled) {
          loaded.destroy();
          return;
        }
        setDoc(loaded);
        setPageCount(loaded.numPages);
        renderedPages.current.clear();
        pageRefs.current = new Array(loaded.numPages).fill(null);

        if (onFirstPageSize) {
          try {
            const firstPage = await loaded.getPage(1);
            if (!cancelled) {
              const v = firstPage.getViewport({ scale: 1 });
              onFirstPageSize({ width: v.width, height: v.height });
            }
          } catch {
            // best-effort; ignore size reporting failures
          }
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
    // onFirstPageSize is intentionally excluded — we only want to refire on path change
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filePath]);

  // Lazy-render pages as they enter the viewport
  const renderPage = useCallback(
    async (pageNum: number) => {
      if (!doc || renderedPages.current.has(pageNum)) return;
      renderedPages.current.add(pageNum);
      try {
        const page = await doc.getPage(pageNum);
        const canvasHost = pageRefs.current[pageNum - 1];
        if (!canvasHost) return;

        const dpr = window.devicePixelRatio || 1;
        const viewport = page.getViewport({ scale: zoom * dpr });
        const canvas = document.createElement("canvas");
        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);
        canvas.style.width = `${Math.floor(viewport.width / dpr)}px`;
        canvas.style.height = `${Math.floor(viewport.height / dpr)}px`;
        canvas.className = "block bg-white shadow-sm";

        const ctx = canvas.getContext("2d");
        if (!ctx) return;

        canvasHost.replaceChildren(canvas);
        await page.render({ canvas, canvasContext: ctx, viewport }).promise;
      } catch (e) {
        console.warn(`Failed to render page ${pageNum}:`, e);
        renderedPages.current.delete(pageNum);
      }
    },
    [doc, zoom],
  );

  // Re-render every visible page when zoom changes
  useEffect(() => {
    if (!doc) return;
    renderedPages.current.clear();
    pageRefs.current.forEach((host, idx) => {
      if (host) {
        // Trigger via observer below
        host.replaceChildren();
      }
      void idx;
    });
    // Visible pages will be picked up by the observer below
  }, [zoom, doc]);

  // Intersection observer: render whatever is near the viewport
  useEffect(() => {
    if (!doc || !containerRef.current) return;
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            const idx = Number(
              (entry.target as HTMLElement).dataset.page ?? "0",
            );
            if (idx > 0) void renderPage(idx);
          }
        });
      },
      {
        root: containerRef.current,
        rootMargin: "200px 0px",
        threshold: 0.01,
      },
    );
    pageRefs.current.forEach((host) => host && observer.observe(host));
    return () => observer.disconnect();
  }, [doc, renderPage]);

  // Scroll to a specific page when requested by chat references
  useEffect(() => {
    if (!scrollToPage || !pageRefs.current[scrollToPage - 1]) return;
    pageRefs.current[scrollToPage - 1]?.scrollIntoView({
      behavior: "smooth",
      block: "start",
    });
  }, [scrollToPage]);

  const handleZoomIn = () => setZoom((z) => Math.min(z * 1.2, 5));
  const handleZoomOut = () => setZoom((z) => Math.max(z / 1.2, 0.2));
  const handleFitWidth = () => {
    if (!containerRef.current || !doc) return;
    void doc.getPage(1).then((page) => {
      const baseViewport = page.getViewport({ scale: 1 });
      const containerWidth = containerRef.current!.clientWidth - 48;
      setZoom(containerWidth / baseViewport.width);
    });
  };

  return (
    <div className="flex h-full flex-col bg-muted/30">
      <div
        ref={containerRef}
        className="flex-1 overflow-y-auto p-4"
      >
        {!filePath && placeholderSize && (
          <div className="mx-auto flex max-w-3xl flex-col gap-4">
            <div
              className="relative overflow-hidden rounded-md border border-border bg-white shadow-sm"
              style={{
                width: Math.floor(placeholderSize.width * zoom),
                height: Math.floor(placeholderSize.height * zoom),
                maxWidth: "100%",
              }}
              aria-label="Translation placeholder page"
            />
          </div>
        )}
        {!filePath && !placeholderSize && emptyState && (
          <div className="flex h-full items-center justify-center">
            {emptyState}
          </div>
        )}
        {loading && (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            Loading PDF…
          </div>
        )}
        {error && (
          <div className="flex h-full items-center justify-center text-sm text-destructive">
            {error}
          </div>
        )}
        {doc && (
          <div className="mx-auto flex max-w-3xl flex-col gap-4">
            {Array.from({ length: pageCount }, (_, i) => (
              <div
                key={i}
                data-page={i + 1}
                ref={(el) => {
                  pageRefs.current[i] = el;
                }}
                className="relative overflow-hidden rounded-md border border-border bg-white"
                style={{
                  minHeight: 200,
                }}
              >
                <div className="flex h-48 items-center justify-center text-xs text-muted-foreground">
                  Page {i + 1}…
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {(doc || label) && (
        <div className="flex h-9 shrink-0 items-center justify-between border-t border-border bg-background px-3 text-xs">
          <div className="flex items-center gap-2 text-muted-foreground">
            {label && <span className="font-medium">{label}</span>}
            {doc && <span>· {pageCount} pages</span>}
          </div>
          {doc && (
            <div className="flex items-center gap-1">
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                onClick={handleZoomOut}
                aria-label="Zoom out"
              >
                <Minus className="h-3.5 w-3.5" />
              </Button>
              <span className="min-w-[42px] text-center font-mono">
                {Math.round(zoom * 100)}%
              </span>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                onClick={handleZoomIn}
                aria-label="Zoom in"
              >
                <Plus className="h-3.5 w-3.5" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                onClick={handleFitWidth}
                aria-label="Fit width"
              >
                <Maximize2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
