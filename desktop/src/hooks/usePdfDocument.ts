import { useEffect, useRef, useState } from "react";
import { GlobalWorkerOptions, getDocument } from "pdfjs-dist";
import type { PDFDocumentLoadingTask, PDFDocumentProxy } from "pdfjs-dist";
import workerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";

import { sidecarToken, sidecarUrl } from "@/lib/api-client";
import {
  accumulateSince,
  latestSeq,
  type ArtifactChange,
  type PendingChanges,
} from "@/lib/pdf-viewer/artifact-swap";
import { pdfAssetUrl } from "@/lib/pdf-viewer/asset-urls";

GlobalWorkerOptions.workerSrc = workerSrc;

export interface LoadedDocument {
  doc: PDFDocumentProxy;
  /** `null`: a different document from the one before. Otherwise the pages
   *  that differ from the version it replaced. */
  changes: PendingChanges | null;
  /** A new version of the document already on screen: keep the reader's
   *  place. */
  keepPosition: boolean;
}

interface Options {
  filePath: string | null;
  /** Bumped to refetch `filePath` even though the path didn't change. */
  reloadKey: number;
  /** A new path is a new version of the same document. See `PdfViewer`. */
  incrementalUpdates: boolean;
  changeLog: readonly ArtifactChange[];
}

/**
 * The pdf.js document behind one viewer: loading it, replacing it, and
 * destroying every one that's been replaced.
 *
 * The destroying matters. Each loaded document holds its parsed file in the
 * worker, and before #30 the translated pane replaced its document after every
 * chunk without ever destroying one.
 *
 * With `incrementalUpdates`, a new path doesn't take the current document down.
 * The new one loads off-screen while the old one stays visible, and arrives
 * with `changes` saying which pages to repaint. If the load fails, the old
 * version stays. That one is still a correct translation, only a less complete
 * one. The next version to load still differs from it by the same pages, so
 * `changes` keeps accumulating.
 */
export function usePdfDocument({
  filePath,
  reloadKey,
  incrementalUpdates,
  changeLog,
}: Options) {
  const [loaded, setLoaded] = useState<LoadedDocument | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // What's on screen, readable from inside a load without re-running it.
  const loadedRef = useRef<LoadedDocument | null>(null);
  const changeLogRef = useRef(changeLog);
  changeLogRef.current = changeLog;
  /** Pages differing from what's on screen, across loads overtaken before they
   *  finished. */
  const pendingRef = useRef<PendingChanges | null>(null);
  /** Newest change-log entry already accounted for. */
  const seenRef = useRef(0);
  const reloadKeyRef = useRef(reloadKey);

  useEffect(() => {
    const show = (next: LoadedDocument | null) => {
      loadedRef.current = next;
      setLoaded(next);
    };
    const reloaded = reloadKeyRef.current !== reloadKey;
    reloadKeyRef.current = reloadKey;

    if (!filePath) {
      pendingRef.current = null;
      seenRef.current = latestSeq(changeLogRef.current);
      show(null);
      setLoading(false);
      setError(null);
      return;
    }

    const incremental = incrementalUpdates && loadedRef.current !== null;
    if (incremental) {
      const caughtUp = accumulateSince(
        pendingRef.current,
        changeLogRef.current,
        seenRef.current,
      );
      // A reload is the same path with different bytes: no page can be trusted.
      pendingRef.current = reloaded ? "all" : caughtUp.pending;
      seenRef.current = caughtUp.seen;
    } else {
      pendingRef.current = null;
      seenRef.current = latestSeq(changeLogRef.current);
      // A different document: take the old one down now rather than leave it
      // on screen under the new file's name, or under its load error.
      show(null);
      setLoading(true);
    }
    setError(null);

    let cancelled = false;
    let task: PDFDocumentLoadingTask | null = null;
    void (async () => {
      try {
        const url = await sidecarUrl(
          `/pdf/file?path=${encodeURIComponent(filePath)}`,
        );
        const token = await sidecarToken();
        if (cancelled) return;
        // pdf.js fetches all three of these at runtime and defaults every one
        // of them to null, warning about none: an unset prefix costs the JPX
        // pages (#73), then the CJK text and the standard-font metrics (#77).
        // See lib/pdf-viewer/asset-urls.ts.
        const base = document.baseURI;
        task = getDocument({
          url,
          httpHeaders: { Authorization: `Bearer ${token}` },
          wasmUrl: pdfAssetUrl("wasm", base),
          cMapUrl: pdfAssetUrl("cmaps", base),
          // Load-bearing on this path, not a restatement of the default:
          // `fetchBuiltInCMap` appends `.bcmap` and sets `isCompressed` from
          // it, so an unset value asks for an extensionless file and then
          // reads the bytes as if they were not compressed.
          cMapPacked: true,
          standardFontDataUrl: pdfAssetUrl("standardFonts", base),
          // Otherwise pdf.js substitutes a non-embedded standard font from
          // whatever the webview has, and the prefix above is consulted for
          // Symbol and ZapfDingbats alone. The substitute's widths are a
          // guess that differs per platform; the bundled Foxit metrics are
          // the real ones, which is the whole point of shipping them.
          useSystemFonts: false,
          // Pinned, because pdf.js would otherwise derive it from whether the
          // base URI is http: Tauri serves http://tauri.localhost on Windows
          // but tauri://localhost on Linux and macOS, so the default would
          // fetch from the worker on Windows and from the main thread
          // everywhere else. One path keeps the CSP surface where #73 left it.
          useWorkerFetch: false,
        });
        const doc = await task.promise;
        if (cancelled) {
          void doc.destroy();
          return;
        }
        const previous = loadedRef.current;
        const sameShape =
          incremental &&
          previous !== null &&
          previous.doc.numPages === doc.numPages;
        show({
          doc,
          changes: sameShape ? (pendingRef.current ?? "all") : null,
          keepPosition: incremental,
        });
        pendingRef.current = null;
        setLoading(false);
      } catch (e) {
        if (cancelled) return;
        if (incremental && loadedRef.current) {
          console.warn(
            `Could not load ${filePath}; keeping the previous version on screen.`,
            e,
          );
        } else {
          setError((e as Error).message);
          setLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
      // Stops the fetch and frees the worker-side transport of a load that
      // was overtaken before it finished.
      void task?.destroy();
    };
    // `incrementalUpdates` is fixed per pane, and the change log is read
    // through its ref when a load starts: neither should start a load.
  }, [filePath, reloadKey]);

  // A replaced document is destroyed in the cleanup of the render that showed
  // it. Cleanups run before the commit's effects, and no other code runs in
  // between, so the viewer hands the renderer the successor straight after.
  useEffect(() => {
    const doc = loaded?.doc;
    return () => {
      if (doc && loadedRef.current?.doc !== doc) void doc.destroy();
    };
  }, [loaded]);

  useEffect(
    () => () => {
      const current = loadedRef.current;
      loadedRef.current = null;
      void current?.doc.destroy();
    },
    [],
  );

  return { loaded, loading, error };
}
