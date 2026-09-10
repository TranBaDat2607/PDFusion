/**
 * Find-in-document over pdf.js text content, as plain data.
 *
 * This works on the `items` of `page.getTextContent()`, the same object the
 * page's `TextLayer` is built from. That is what lets a match found here be
 * painted there: `TextLayer` creates exactly one `textDivs` entry for every
 * item that has a `str`, empty strings included, in order, and skips
 * marked-content entries (which have no `str`). So an item number here is an
 * index into `textDivs`.
 *
 * Matching ignores case and diacritics: "viet" finds "Việt", and "dang" finds
 * "đang". Chrome's find and pdf.js's own find both ignore diacritics by default,
 * and this app's output is usually Vietnamese, where typing without them is
 * normal. Runs of whitespace match a single space, and a line break inside a
 * paragraph counts as a space, so a phrase that wraps is still found.
 */

export interface FindTextItem {
  /** Absent on marked-content entries, which carry no text. */
  str?: string;
  hasEOL?: boolean;
  /** What a marked-content entry has instead of `str`. */
  type?: string;
}

/** A page's folded text, plus where each of its UTF-16 units came from. */
export interface PageTextIndex {
  text: string;
  /** Per unit of `text`: which text item it came from… */
  items: number[];
  /** …and the span `[from, to)` of that item's `str` it stands for. A line
   *  break's space has `from === to`, the end of the item it follows. */
  froms: number[];
  tos: number[];
}

/** A match in a page's folded text, `end` exclusive. */
export interface TextMatch {
  start: number;
  end: number;
}

export interface DocumentMatch extends TextMatch {
  page: number;
}

/** A part of one text item to highlight, `to` exclusive. */
export interface ItemRange {
  item: number;
  from: number;
  to: number;
}

const COMBINING_MARKS = /\p{M}/gu;

function isSpace(ch: string): boolean {
  const code = ch.charCodeAt(0);
  if (code < 128) return code === 32 || (code >= 9 && code <= 13);
  return /\s/u.test(ch);
}

function foldChar(ch: string): string {
  const code = ch.charCodeAt(0);
  // ASCII is nearly all of it, and doesn't need Unicode normalization.
  if (code < 128) {
    return code >= 65 && code <= 90 ? String.fromCharCode(code + 32) : ch;
  }
  // Marks go before lowercasing: "İ".toLowerCase() is "i" plus a combining dot.
  // "đ" is its own letter rather than "d" plus a mark, so NFD leaves it alone.
  return ch
    .normalize("NFD")
    .replace(COMBINING_MARKS, "")
    .toLowerCase()
    .replace(/đ/g, "d");
}

type Emit = (item: number, from: number, to: number, units: number) => void;

/** The one folding pass. `pageText` and `buildPageIndex` both go through it, so
 *  the text a search counts matches in is the text a highlight maps back from. */
function fold(items: readonly FindTextItem[], emit: Emit | null): string {
  let text = "";
  // Starts true so leading whitespace is dropped.
  let lastWasSpace = true;
  let item = -1;
  for (const entry of items) {
    if (typeof entry.str !== "string") continue;
    item++;
    const str = entry.str;
    let offset = 0;
    for (const ch of str) {
      const next = offset + ch.length;
      if (isSpace(ch)) {
        if (!lastWasSpace) {
          text += " ";
          emit?.(item, offset, next, 1);
          lastWasSpace = true;
        }
      } else {
        const folded = foldChar(ch);
        if (folded) {
          text += folded;
          emit?.(item, offset, next, folded.length);
          lastWasSpace = false;
        }
      }
      offset = next;
    }
    if (entry.hasEOL && !lastWasSpace) {
      text += " ";
      emit?.(item, str.length, str.length, 1);
      lastWasSpace = true;
    }
  }
  return text;
}

/** A page's folded text alone. Cheap enough to keep for every page of a
 *  document, which is what counting matches needs. */
export function pageText(items: readonly FindTextItem[]): string {
  return fold(items, null);
}

/** A page's folded text with a map back to its items. Only needed for pages
 *  being highlighted, which are only ever the few that are rendered. */
export function buildPageIndex(items: readonly FindTextItem[]): PageTextIndex {
  const index: PageTextIndex = { text: "", items: [], froms: [], tos: [] };
  index.text = fold(items, (item, from, to, units) => {
    for (let k = 0; k < units; k++) {
      index.items.push(item);
      index.froms.push(from);
      index.tos.push(to);
    }
  });
  return index;
}

/** A query folded the same way as page text. Empty means "nothing to find". */
export function foldQuery(query: string): string {
  return fold([{ str: query }], null).trim();
}

/** Non-overlapping matches of an already-folded `needle` in folded `text`. */
export function findInText(text: string, needle: string): TextMatch[] {
  const matches: TextMatch[] = [];
  if (!needle) return matches;
  let from = 0;
  for (;;) {
    const start = text.indexOf(needle, from);
    if (start < 0) break;
    matches.push({ start, end: start + needle.length });
    from = start + needle.length;
  }
  return matches;
}

/** The parts of each text item a match covers, for painting it over the text
 *  layer. A match that runs across items gets one range per item. */
export function matchToItemRanges(
  index: PageTextIndex,
  match: TextMatch,
): ItemRange[] {
  const ranges: ItemRange[] = [];
  const end = Math.min(match.end, index.items.length);
  for (let i = match.start; i < end; i++) {
    const item = index.items[i];
    const last = ranges[ranges.length - 1];
    if (last && last.item === item) {
      // Widening rather than appending also covers what folding dropped from
      // the middle of a match: collapsed spaces, stripped combining marks.
      last.from = Math.min(last.from, index.froms[i]);
      last.to = Math.max(last.to, index.tos[i]);
    } else {
      ranges.push({ item, from: index.froms[i], to: index.tos[i] });
    }
  }
  return ranges.filter((range) => range.to > range.from);
}

/** The match after (or before) `current`, wrapping round the document. With no
 *  current match, the first (or last) one. */
export function stepMatch(
  total: number,
  current: number | null,
  direction: 1 | -1,
): number | null {
  if (total <= 0) return null;
  if (current == null) return direction === 1 ? 0 : total - 1;
  return (((current + direction) % total) + total) % total;
}

/** The first match on `page` or later, wrapping to the first match overall.
 *  `matches` is in document order. */
export function firstMatchFrom(
  matches: readonly DocumentMatch[],
  page: number,
): number | null {
  if (matches.length === 0) return null;
  let lo = 0;
  let hi = matches.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (matches[mid].page >= page) {
      found = mid;
      hi = mid - 1;
    } else {
      lo = mid + 1;
    }
  }
  return found < 0 ? 0 : found;
}
