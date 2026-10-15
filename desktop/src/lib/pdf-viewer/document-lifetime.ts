import type {
  PDFDocumentLoadingTask,
  PDFDocumentProxy,
} from "pdfjs-dist";

/** A pdf.js load its owner may walk away from before or after it finishes. */
export interface DocumentLoad {
  /** The document, as the task's own promise resolves it. */
  promise: Promise<PDFDocumentProxy>;
  /**
   * Walk away from the load. A document still on its way is stopped and its
   * worker freed. One that has already arrived is left alone: it belongs to
   * whoever showed it.
   */
  abandon(): void;
}

export function trackDocumentLoad(task: PDFDocumentLoadingTask): DocumentLoad {
  let arrived = false;
  // Set ahead of any caller's own continuation, which is chained after this
  // one: a caller that sees the document can trust `abandon` to spare it.
  const promise = task.promise.then((doc) => {
    arrived = true;
    return doc;
  });
  return {
    promise,
    abandon: () => {
      // Destroying the task destroys its document too. The effect cleanup
      // that calls this runs on every new path or reload, while the document
      // it loaded is still the one on screen; destroying it there left every
      // later `getPage` on it throwing (see `cleanupPage`), which took the
      // whole window down mid re-translate.
      if (!arrived) void task.destroy();
    },
  };
}

/**
 * Hand page `page`'s operator list and decoded images back to pdf.js, if
 * `stillReleased()` still says so once the page proxy arrives. Never throws,
 * and leaves no rejection unhandled.
 */
export function cleanupPage(
  doc: Pick<PDFDocumentProxy, "getPage"> | null,
  page: number,
  stillReleased: () => boolean,
): void {
  let proxy: ReturnType<PDFDocumentProxy["getPage"]> | undefined;
  try {
    proxy = doc?.getPage(page);
  } catch {
    // A destroyed document's `getPage` throws rather than rejecting: its
    // message handler is already null. Its resources went with it.
    return;
  }
  void proxy?.then(
    (pageProxy) => {
      if (stillReleased()) pageProxy.cleanup();
    },
    () => undefined,
  );
}
