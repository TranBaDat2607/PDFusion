/**
 * The document in a set of paths dragged onto the window: the first `.pdf`,
 * matched case-insensitively. Same rule as the shell's `first_pdf_argument`
 * (`lib.rs`) for a document named on the command line. The sidecar checks the
 * file itself when the viewer asks for it.
 */
export function firstPdfPath(paths: readonly string[]): string | null {
  return paths.find((path) => /\.pdf$/i.test(path)) ?? null;
}
