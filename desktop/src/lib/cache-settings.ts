/**
 * The numbers and words Settings → Cache shows (#32). Pure, so they run under
 * vitest's node environment, like `chat-documents.ts`.
 */

export type CacheTarget = "paragraph" | "pdf";
export type ClearScope = "expired" | "all";

/** A size the sidecar reports in megabytes, for a stat tile. */
export function formatMegabytes(mb: number): string {
  if (mb <= 0) return "0 MB";
  if (mb < 1) return `${Math.max(1, Math.round(mb * 1024))} KB`;
  if (mb < 10) return `${mb.toFixed(1)} MB`;
  return `${Math.round(mb)} MB`;
}

/** What the dialog says before a cache is emptied: exactly what goes, and
 *  what stays. The tab used to clear both caches under a button that named
 *  neither. */
export function clearConfirmation(target: CacheTarget): {
  title: string;
  description: string;
  action: string;
} {
  if (target === "pdf") {
    return {
      title: "Clear the translated PDF cache?",
      description:
        "This deletes every translated PDF PDFusion has kept, so translating one of those PDFs again runs the whole pipeline. Translated paragraphs are kept, and so are PDFs you saved.",
      action: "Clear PDFs",
    };
  }
  return {
    title: "Clear the paragraph cache?",
    description:
      "This deletes every translated paragraph PDFusion has kept, so translating them again goes back to the translation service. Translated PDFs are kept, and so are PDFs you saved.",
    action: "Clear paragraphs",
  };
}

/** The toast after a Clear. */
export function describeCleared(
  removed: number,
  target: CacheTarget,
  scope: ClearScope,
): string {
  const plural = removed === 1 ? "" : "s";
  if (target === "pdf") return `Removed ${removed} translated PDF${plural}`;
  const kind = scope === "expired" ? "expired paragraph" : "paragraph";
  return `Removed ${removed} ${kind}${plural}`;
}
