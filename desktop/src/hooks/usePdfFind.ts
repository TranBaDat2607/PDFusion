import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PDFDocumentProxy } from "pdfjs-dist";

import {
  clearHighlights,
  paintHighlights,
  type HighlightRange,
} from "@/components/pdf-viewer/find-highlight";
import type { PageRenderer } from "@/components/pdf-viewer/page-renderer";
import type { PendingChanges } from "@/lib/pdf-viewer/artifact-swap";
import {
  buildPageIndex,
  findInText,
  firstMatchFrom,
  foldQuery,
  matchToItemRanges,
  pageText,
  stepMatch,
  type DocumentMatch,
} from "@/lib/pdf-viewer/find";

/** Typing pause before a search starts. */
const SEARCH_DEBOUNCE_MS = 200;
/** How often a running search publishes its match count, in pages read. */
const PUBLISH_EVERY_PAGES = 10;

interface Options {
  doc: PDFDocumentProxy | null;
  /** How `doc` differs from the document before it. See `usePdfDocument`. */
  changes: PendingChanges | null;
  renderer: PageRenderer;
  /** The page the reader is on. The first match picked is on it or after it. */
  currentPage: () => number;
  /** Scroll a page into view if it isn't, so its text layer gets built. */
  revealPage: (page: number) => void;
  /** Scroll a highlighted match into the middle of the view. */
  revealElement: (element: HTMLElement) => void;
  onClose: () => void;
}

export interface PdfFind {
  open: boolean;
  query: string;
  setQuery: (query: string) => void;
  matchCount: number;
  selected: number | null;
  searching: boolean;
  /** Changes whenever the find bar should take focus. */
  focusKey: number;
  openFind: () => void;
  close: () => void;
  next: () => void;
  previous: () => void;
}

/**
 * Find-in-document for one viewer (#30).
 *
 * The search reads every page's text from the document itself, not from text
 * layers, which only exist for the few pages that are rendered. It keeps each
 * page's folded text, so refining a query doesn't extract a long document again.
 * Highlights are painted only on rendered pages, and are repainted as pages
 * render: moving to a match on page 200 scrolls there, and the match lights up
 * once that page's text layer exists.
 *
 * During a streaming translation the document is swapped after every chunk. An
 * open search then re-runs quietly against the new version (unchanged pages keep
 * their cached text) and keeps its place, rather than jumping the reader back
 * to the first match.
 */
export function usePdfFind(options: Options): PdfFind {
  const { doc, renderer } = options;
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<DocumentMatch[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [searching, setSearching] = useState(false);
  const [focusKey, setFocusKey] = useState(0);

  const openRef = useRef(open);
  openRef.current = open;
  const matchesRef = useRef(matches);
  matchesRef.current = matches;
  const selectedRef = useRef(selected);
  selectedRef.current = selected;

  const matchesByPage = useMemo(() => {
    const byPage = new Map<number, number[]>();
    matches.forEach((match, i) => {
      const list = byPage.get(match.page);
      if (list) list.push(i);
      else byPage.set(match.page, [i]);
    });
    return byPage;
  }, [matches]);
  const matchesByPageRef = useRef(matchesByPage);
  matchesByPageRef.current = matchesByPage;

  /** Folded text of each page read so far, for the document it was read from. */
  const textsRef = useRef<{
    doc: PDFDocumentProxy | null;
    pages: Map<number, string>;
  }>({ doc: null, pages: new Map() });
  /** The query and document of the search that last started. */
  const searchedRef = useRef<{ needle: string; doc: PDFDocumentProxy } | null>(
    null,
  );
  /** Per rendered page, the text spans its highlights changed. */
  const touchedRef = useRef(new Map<number, Set<number>>());
  /** A match to scroll to once its highlight exists. */
  const pendingRevealRef = useRef<number | null>(null);

  // Move the text cache to a new version of the same document, less the pages
  // that changed. Runs on every document change, so the cache always belongs
  // to the version `changes` is relative to.
  useEffect(() => {
    const cache = textsRef.current;
    if (cache.doc === doc) return;
    const { changes } = optionsRef.current;
    const pages = new Map<number, string>();
    if (doc && cache.doc && changes !== null && changes !== "all") {
      for (const [page, text] of cache.pages) {
        if (!changes.has(page)) pages.set(page, text);
      }
    }
    textsRef.current = { doc, pages };
  }, [doc]);

  /** Repaint one page's highlights. Returns the current match's element when
   *  it's on this page. */
  const paintPage = useCallback(
    (page: number): HTMLElement | null => {
      const layer = renderer.textLayer(page);
      if (!layer) return null;
      const touched = touchedRef.current.get(page) ?? new Set<number>();
      const indices = openRef.current
        ? matchesByPageRef.current.get(page)
        : undefined;
      const text = textsRef.current.pages.get(page);
      const index =
        indices && text !== undefined ? buildPageIndex(layer.items) : null;
      // A layer built from an older version of the page than the one searched
      // (a translation swapped it in since) has different offsets. Leave it bare.
      if (!indices || !index || index.text !== text) {
        if (touched.size > 0) clearHighlights(layer.divs, layer.strs, touched);
        touchedRef.current.delete(page);
        return null;
      }
      const ranges: HighlightRange[] = [];
      for (const i of indices) {
        const isSelected = i === selectedRef.current;
        for (const range of matchToItemRanges(index, matchesRef.current[i])) {
          ranges.push({ ...range, selected: isSelected });
        }
      }
      const painted = paintHighlights(layer.divs, layer.strs, ranges, touched);
      touchedRef.current.set(page, painted.touched);
      return painted.selected;
    },
    [renderer],
  );

  const select = useCallback(
    (i: number) => {
      const match = matchesRef.current[i];
      if (!match) return;
      pendingRevealRef.current = i;
      optionsRef.current.revealPage(match.page);
      if (i !== selectedRef.current) {
        selectedRef.current = i;
        setSelected(i); // the paint effect scrolls to it
        return;
      }
      // The same match again: nothing re-renders, so scroll to it here.
      const element = paintPage(match.page);
      if (element) {
        pendingRevealRef.current = null;
        optionsRef.current.revealElement(element);
      }
    },
    [paintPage],
  );

  const handleTextLayer = useCallback(
    (page: number) => {
      const element = paintPage(page);
      const pending = pendingRevealRef.current;
      if (
        element &&
        pending !== null &&
        pending === selectedRef.current &&
        matchesRef.current[pending]?.page === page
      ) {
        pendingRevealRef.current = null;
        optionsRef.current.revealElement(element);
      }
    },
    [paintPage],
  );

  useEffect(() => {
    renderer.events = {
      onTextLayer: handleTextLayer,
      onRelease: (page) => {
        touchedRef.current.delete(page);
      },
    };
    return () => {
      renderer.events = {};
    };
  }, [renderer, handleTextLayer]);

  useEffect(() => {
    const needle = foldQuery(query);
    const active = open && doc !== null && needle !== "";
    const lastSearch = searchedRef.current;
    const refresh =
      active &&
      lastSearch !== null &&
      lastSearch.needle === needle &&
      lastSearch.doc !== doc;
    searchedRef.current = active && doc ? { needle, doc } : null;

    if (!refresh) {
      matchesRef.current = [];
      selectedRef.current = null;
      pendingRevealRef.current = null;
      setMatches([]);
      setSelected(null);
    }
    if (!active || !doc) {
      setSearching(false);
      return;
    }

    const texts =
      textsRef.current.doc === doc
        ? textsRef.current.pages
        : new Map<number, string>();
    let cancelled = false;
    setSearching(true);

    const run = async () => {
      const startPage = optionsRef.current.currentPage();
      const found: DocumentMatch[] = [];
      let picked = refresh;
      for (let page = 1; page <= doc.numPages; page++) {
        let text = texts.get(page);
        if (text === undefined) {
          try {
            const content = await (await doc.getPage(page)).getTextContent();
            text = pageText(content.items);
            texts.set(page, text);
          } catch {
            // No matches on a page whose text can't be read. Not cached, so a
            // later search tries it again.
            text = "";
          }
          if (cancelled) return;
        }
        for (const match of findInText(text, needle)) {
          found.push({ page, ...match });
        }

        const last = page === doc.numPages;
        if (last || (!refresh && page % PUBLISH_EVERY_PAGES === 0)) {
          const snapshot = found.slice();
          matchesRef.current = snapshot;
          setMatches(snapshot);
          if (!picked) {
            const first = firstMatchFrom(snapshot, startPage);
            // Before the last page, only a match at or after the reader counts:
            // a better one may still be ahead.
            if (first !== null && (last || snapshot[first].page >= startPage)) {
              picked = true;
              select(first);
            }
          }
        }
      }
      if (
        refresh &&
        selectedRef.current !== null &&
        selectedRef.current >= found.length
      ) {
        const clamped = found.length > 0 ? found.length - 1 : null;
        selectedRef.current = clamped;
        setSelected(clamped);
      }
      setSearching(false);
    };

    // A new version of the document is re-searched straight away; a query
    // waits for the typing to pause.
    const timer = window.setTimeout(
      () => void run(),
      refresh ? 0 : SEARCH_DEBOUNCE_MS,
    );
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [open, query, doc, select]);

  useEffect(() => {
    const selectedPage =
      selected !== null ? matches[selected]?.page : undefined;
    let selectedElement: HTMLElement | null = null;
    for (const page of renderer.textLayerPages()) {
      const element = paintPage(page);
      if (page === selectedPage) selectedElement = element;
    }
    if (
      selectedElement &&
      pendingRevealRef.current !== null &&
      pendingRevealRef.current === selected
    ) {
      pendingRevealRef.current = null;
      optionsRef.current.revealElement(selectedElement);
    }
  }, [open, matches, selected, renderer, paintPage]);

  const openFind = useCallback(() => {
    setOpen(true);
    setFocusKey((key) => key + 1);
  }, []);

  const close = useCallback(() => {
    setOpen(false);
    optionsRef.current.onClose();
  }, []);

  const step = useCallback(
    (direction: 1 | -1) => {
      // F3 with the bar closed opens it, like a browser.
      if (!openRef.current) {
        openFind();
        return;
      }
      const i = stepMatch(
        matchesRef.current.length,
        selectedRef.current,
        direction,
      );
      if (i !== null) select(i);
    },
    [openFind, select],
  );
  const next = useCallback(() => step(1), [step]);
  const previous = useCallback(() => step(-1), [step]);

  return {
    open,
    query,
    setQuery,
    matchCount: matches.length,
    selected,
    searching,
    focusKey,
    openFind,
    close,
    next,
    previous,
  };
}
