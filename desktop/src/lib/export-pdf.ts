/**
 * Saving a translated PDF to a permanent, user-owned location.
 *
 * The translation pipeline only ever produces throwaway artifacts: a rolling
 * file in `%TEMP%\pdfusion-translate-<rand>\` (wiped by the next job, by app
 * exit, and by the sidecar's orphan sweep) plus an LRU-evictable entry in the
 * whole-PDF cache. Neither is something the user owns, so "save" here means
 * copying to a path they picked in the native Save dialog.
 *
 * The orchestration below takes its collaborators as arguments instead of
 * importing them, so the save/cancel/failure paths are unit-testable without a
 * Tauri runtime or a DOM.
 */

// ---------------------------------------------------------------------------
// Path helpers — the sidecar hands us Windows paths, but dev-mode/tests may
// use POSIX ones, so everything here handles both separators.
// ---------------------------------------------------------------------------

/** Separator used by a path; defaults to the platform-ish `\` on Windows paths. */
function pathSeparator(path: string): string {
  return path.includes("\\") ? "\\" : "/";
}

export function basename(path: string): string {
  const trimmed = path.replace(/[\\/]+$/, "");
  const idx = Math.max(trimmed.lastIndexOf("\\"), trimmed.lastIndexOf("/"));
  return idx === -1 ? trimmed : trimmed.slice(idx + 1);
}

/** Directory portion of a path, or "" when the path has no directory part. */
export function dirname(path: string): string {
  const trimmed = path.replace(/[\\/]+$/, "");
  const idx = Math.max(trimmed.lastIndexOf("\\"), trimmed.lastIndexOf("/"));
  if (idx === -1) return "";
  // Keep the root separator for paths like `C:\file.pdf` / `/file.pdf`.
  if (idx === 0) return trimmed.slice(0, 1);
  return trimmed.slice(0, idx);
}

/** Filename without its final extension. `paper.tar.pdf` → `paper.tar`. */
export function stripExtension(name: string): string {
  const idx = name.lastIndexOf(".");
  // A leading dot is part of the name (`.hidden`), not an extension.
  return idx <= 0 ? name : name.slice(0, idx);
}

function joinPath(dir: string, name: string): string {
  if (!dir) return name;
  const sep = pathSeparator(dir);
  return `${dir.replace(/[\\/]+$/, "")}${sep}${name}`;
}

/**
 * Default filename for a saved translation: `<original stem>_<lang>.pdf`.
 *
 * The suffix is always appended, even when the stem already ends in it, so
 * `paper_vi.pdf` suggests `paper_vi_vi.pdf`. Skipping it when the stem "looks
 * already translated" guesses intent from a filename — `chapter_vi.pdf` is a
 * Roman numeral, not a translation — and, worse, that guess makes the
 * suggestion collide with the source document itself. Always appending is
 * uglier in one case and collision-free by construction in every case.
 */
export function suggestedExportName(
  originalPath: string,
  targetLang: string,
): string {
  const stem = stripExtension(basename(originalPath)) || "translated";
  const lang = (targetLang || "vi").toLowerCase();
  return `${stem}_${lang}.pdf`;
}

/** Full default path for the Save dialog: the suggested name beside the source. */
export function suggestedExportPath(
  originalPath: string,
  targetLang: string,
): string {
  return joinPath(
    dirname(originalPath),
    suggestedExportName(originalPath, targetLang),
  );
}

// ---------------------------------------------------------------------------
// Orchestration
// ---------------------------------------------------------------------------

export interface ExportedFile {
  saved_path: string;
  bytes_written: number;
}

export interface ExportDeps {
  /** Native Save dialog. Resolves to `null` when the user cancels. */
  showSaveDialog: (options: {
    defaultPath: string;
    filters: Array<{ name: string; extensions: string[] }>;
    title: string;
  }) => Promise<string | null>;
  /** `POST /pdf/export` — copies `source` to `destination` on the sidecar side.
   *  `protectPath` is the user's own document. The Save dialog lets them type
   *  its name and confirm "Replace?", which would destroy their input with no
   *  undo, so the sidecar refuses it as a destination. */
  exportPdf: (
    source: string,
    destination: string,
    protectPath: string | null,
  ) => Promise<ExportedFile>;
}

export interface ExportParams {
  /** The artifact the translation job produced (temp path or cache copy). */
  sourcePath: string | null;
  /** The document the user opened — seeds the default folder and name. */
  originalPath: string | null;
  targetLang: string;
}

export type ExportOutcome =
  | { status: "saved"; path: string; bytes: number }
  | { status: "cancelled" }
  | { status: "error"; message: string };

/**
 * Run the full save flow: pick a destination, then have the sidecar copy the
 * file there.
 *
 * Cancelling the dialog is a normal outcome, not an error — callers must not
 * surface it as a failure.
 */
export async function runExport(
  params: ExportParams,
  deps: ExportDeps,
): Promise<ExportOutcome> {
  const { sourcePath, originalPath, targetLang } = params;
  if (!sourcePath) {
    return {
      status: "error",
      message: "There is no translated PDF to save yet.",
    };
  }

  // Default beside the source document. With no source document loaded we
  // pass a bare filename so the dialog picks its own folder — seeding it from
  // `sourcePath` would propose saving back into the temp dir this whole
  // feature exists to get out of.
  const defaultPath = originalPath
    ? suggestedExportPath(originalPath, targetLang)
    : suggestedExportName(sourcePath, targetLang);

  let destination: string | null;
  try {
    destination = await deps.showSaveDialog({
      defaultPath,
      filters: [{ name: "PDF document", extensions: ["pdf"] }],
      title: "Save translated PDF",
    });
  } catch (e) {
    return { status: "error", message: (e as Error).message };
  }

  if (!destination) return { status: "cancelled" };

  // The dialog can hand back an extension-less name when the user types one
  // without a suffix; the sidecar rejects non-.pdf destinations, so fix it here.
  const withSuffix = destination.toLowerCase().endsWith(".pdf")
    ? destination
    : `${destination}.pdf`;

  try {
    const result = await deps.exportPdf(sourcePath, withSuffix, originalPath);
    return {
      status: "saved",
      path: result.saved_path,
      bytes: result.bytes_written,
    };
  } catch (e) {
    return { status: "error", message: (e as Error).message };
  }
}

/** Human-readable size for the save confirmation toast. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}
