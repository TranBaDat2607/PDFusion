/**
 * Painting find matches onto a rendered page's text layer, the way pdf.js's own
 * `TextHighlighter` does it: the matched part of a text span is wrapped in a
 * `<span class="highlight appended">`, and clearing a span puts back its
 * original text from `textContentItemsStr`.
 *
 * Restoring from that array is safe because nothing else in the app writes into
 * a text span. TextLayer set each span's content to exactly that string.
 */

import type { ItemRange } from "@/lib/pdf-viewer/find";

export interface HighlightRange extends ItemRange {
  /** Part of the current match, which gets the stronger colour. */
  selected: boolean;
}

function restore(
  divs: readonly HTMLElement[],
  strs: readonly string[],
  item: number,
): void {
  const div = divs[item];
  if (div) div.textContent = strs[item] ?? "";
}

/** Put back the original text of every span in `touched`. */
export function clearHighlights(
  divs: readonly HTMLElement[],
  strs: readonly string[],
  touched: Iterable<number>,
): void {
  for (const item of touched) restore(divs, strs, item);
}

/**
 * Highlight `ranges` on one text layer, first restoring any span that
 * `previouslyTouched` changed and this paint doesn't.
 *
 * Returns the spans now changed (pass them back in next time) and the selected
 * match's first highlight element, if it's on this page.
 */
export function paintHighlights(
  divs: readonly HTMLElement[],
  strs: readonly string[],
  ranges: readonly HighlightRange[],
  previouslyTouched: Iterable<number>,
): { touched: Set<number>; selected: HTMLElement | null } {
  const byItem = new Map<number, HighlightRange[]>();
  for (const range of ranges) {
    const list = byItem.get(range.item);
    if (list) list.push(range);
    else byItem.set(range.item, [range]);
  }
  for (const item of previouslyTouched) {
    if (!byItem.has(item)) restore(divs, strs, item);
  }

  const touched = new Set<number>();
  let selected: HTMLElement | null = null;
  for (const [item, itemRanges] of byItem) {
    const div = divs[item];
    const str = strs[item];
    // TextLayer stops creating spans past a very large item count.
    if (!div || str === undefined) continue;

    itemRanges.sort((a, b) => a.from - b.from);
    const fragment = document.createDocumentFragment();
    let position = 0;
    for (const range of itemRanges) {
      const from = Math.max(range.from, position);
      const to = Math.min(range.to, str.length);
      if (to <= from) continue;
      if (from > position) fragment.append(str.slice(position, from));
      const mark = document.createElement("span");
      mark.className = range.selected
        ? "highlight appended selected"
        : "highlight appended";
      mark.textContent = str.slice(from, to);
      fragment.append(mark);
      if (range.selected && !selected) selected = mark;
      position = to;
    }
    if (position < str.length) fragment.append(str.slice(position));
    div.replaceChildren(fragment);
    touched.add(item);
  }
  return { touched, selected };
}
