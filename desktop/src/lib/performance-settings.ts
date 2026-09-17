/**
 * What Settings → Cache → Performance offers for the two translation limits
 * (#33), and what it says about them. Pure, so it runs under vitest's node
 * environment, like `cache-settings.ts`.
 *
 * The bounds are the sidecar's (`config/models.py:MaxPages`,
 * `MaxFileSizeMB`); every preset sits inside them.
 */

export interface LimitOption {
  value: number;
  label: string;
}

export const PAGE_LIMIT_PRESETS = [10, 25, 50, 75, 100] as const;
export const SIZE_LIMIT_PRESETS_MB = [25, 50, 100, 150, 200] as const;

/**
 * The presets, plus the saved value when it isn't one of them — a
 * hand-edited `config.toml` can hold any number within the bounds, and a
 * Select that can't show its own value reads as blank.
 */
export function limitOptions(
  presets: readonly number[],
  current: number | undefined,
  format: (value: number) => string,
): LimitOption[] {
  const values =
    current === undefined || presets.includes(current)
      ? [...presets]
      : [...presets, current].sort((a, b) => a - b);
  return values.map((value) => ({ value, label: format(value) }));
}

export const formatPageLimit = (pages: number): string =>
  `${pages} page${pages === 1 ? "" : "s"}`;

export const formatSizeLimit = (mb: number): string => `${mb} MB`;

/** Why a bigger number costs more — the real reasons, not RAM: memory is set
 *  by "Parallel pages", since the pipeline holds one page per job at a time. */
export const PAGE_LIMIT_HELP =
  "The most pages one translation covers. A longer PDF is translated part by " +
  "part: type the pages in the Pages box next to the file name. After every " +
  "page, PDFusion saves the whole translated PDF again, so each page of a " +
  "long PDF takes a little longer than the same page of a short one.";

export const SIZE_LIMIT_HELP =
  "The largest PDF PDFusion will translate, whichever pages you choose. Each " +
  "save writes a full copy of the document, and two copies are kept on disk " +
  "while a translation runs.";
