/**
 * The toolbar's Pages box (#33): what the user typed, as the `page_ranges`
 * that `POST /translate` takes.
 *
 * The sidecar checks the selection again (`processors/page_selection.py`) and
 * answers 422 with a sentence. This module exists so the toolbar can say so
 * before anything starts. Pure, so it runs under vitest's node environment.
 */

import { pluralizePages } from "@/lib/translation-progress";

/** `[first, last]`, 1-indexed and inclusive — the wire format. */
export type PageRange = [number, number];

export type ParsedPages =
  | { ok: true; ranges: PageRange[] | null }
  | { ok: false; error: string };

/** `TranslateRequest.page_ranges`'s `max_length` in `api/schemas.py`. */
export const MAX_RANGES = 500;

const TOKEN = /^(\d+)(?:-(\d*))?$/;

/** Sorted, with overlapping and touching ranges joined. */
function mergeRanges(ranges: PageRange[]): PageRange[] {
  const sorted = [...ranges].sort((a, b) => a[0] - b[0]);
  const merged: PageRange[] = [];
  for (const [first, last] of sorted) {
    const previous = merged[merged.length - 1];
    if (previous && first <= previous[1] + 1) {
      previous[1] = Math.max(previous[1], last);
    } else {
      merged.push([first, last]);
    }
  }
  return merged;
}

/**
 * Read the Pages box. Blank or "all" is the whole document (`null`), and so is
 * a selection of every page. Accepts `3`, `1-20`, `1–20` and `40-` (to the
 * end), separated by commas, semicolons or spaces.
 *
 * `pageCount` is `null` until the viewer has loaded the document; pages past
 * the end are then left for the sidecar to refuse.
 */
export function parsePageRanges(
  text: string,
  pageCount: number | null,
): ParsedPages {
  const normalized = text.trim().replace(/\s*[-–—]\s*/g, "-");
  if (normalized === "" || /^all$/i.test(normalized)) {
    return { ok: true, ranges: null };
  }

  const ranges: PageRange[] = [];
  for (const token of normalized.split(/[\s,;]+/).filter(Boolean)) {
    const match = TOKEN.exec(token);
    if (!match) {
      return {
        ok: false,
        error: `"${token}" isn't a page or a range, like 35 or 1-20.`,
      };
    }
    const first = Number(match[1]);
    let last: number;
    if (match[2] === undefined) {
      last = first;
    } else if (match[2] === "") {
      if (pageCount == null) {
        return {
          ok: false,
          error: `Name the last page of ${first}-, like ${first}-${first + 9}.`,
        };
      }
      last = pageCount;
    } else {
      last = Number(match[2]);
    }
    if (first < 1) return { ok: false, error: "Page numbers start at 1." };
    if (first > last) {
      return { ok: false, error: `${first}-${last} ends before it starts.` };
    }
    if (pageCount != null && last > pageCount) {
      return {
        ok: false,
        error: `This PDF has ${pluralizePages(pageCount)}, so there is no page ${last}.`,
      };
    }
    ranges.push([first, last]);
  }

  const merged = mergeRanges(ranges);
  if (merged.length > MAX_RANGES) {
    return {
      ok: false,
      error: `That's ${merged.length} separate ranges; use at most ${MAX_RANGES}.`,
    };
  }
  if (
    pageCount != null &&
    merged.length === 1 &&
    merged[0][0] === 1 &&
    merged[0][1] === pageCount
  ) {
    return { ok: true, ranges: null };
  }
  return { ok: true, ranges: merged };
}

export function countPages(ranges: PageRange[]): number {
  return ranges.reduce((total, [first, last]) => total + last - first + 1, 0);
}

/** `"1–20, 35"`. Pass `"-"` for text the user will edit. */
export function formatPageRanges(ranges: PageRange[], dash = "–"): string {
  return ranges
    .map(([first, last]) => (first === last ? `${first}` : `${first}${dash}${last}`))
    .join(", ");
}

/** The hint after the Pages box: "of 120" beside its "All" placeholder, or
 *  "21 of 120" for a selection. */
export function describeSelection(
  parsed: ParsedPages,
  pageCount: number | null,
): string | null {
  if (!parsed.ok || pageCount == null) return null;
  if (parsed.ranges === null) return `of ${pageCount}`;
  return `${countPages(parsed.ranges)} of ${pageCount}`;
}
