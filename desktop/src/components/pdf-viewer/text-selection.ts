/**
 * Drag-selection for pdf.js text layers, ported from pdf.js's
 * `TextLayerBuilder` (pdfjs-dist 5.7.284, web/pdf_viewer.mjs: `#bindMouse` and
 * `#enableGlobalSelectionListener`). Re-check it against that file when
 * pdfjs-dist is bumped.
 *
 * A text layer is transparent spans scattered over the page with empty space
 * between them. Drag a selection across that space and Chromium extends it to
 * whatever node comes next in the DOM, usually the end of the layer, so half
 * the page lights up at once. pdf.js's fix is an `endOfContent` element plus a
 * `selecting` class that lets it cover the layer during a drag; on every
 * selection change the element moves to just after the selection's moving end.
 *
 * This is a port rather than an import because `pdf_viewer.mjs` reads
 * `globalThis.pdfjsLib` as soon as it's evaluated. Importing it would only work
 * while `pdfjs-dist` happened to be evaluated first, and it would bring the
 * whole generic viewer along. pdf.js skips this on Firefox; this app only runs
 * in WebView2, so that branch is gone.
 */

/** Each attached text layer, and its `endOfContent` element. */
const layers = new Map<HTMLElement, HTMLElement>();
let globalListeners: AbortController | null = null;

function reset(end: HTMLElement, layer: HTMLElement): void {
  layer.append(end);
  end.style.width = "";
  end.style.height = "";
  layer.classList.remove("selecting");
}

/** Give a rendered text layer pdf.js's selection behaviour. Returns a detach
 *  function, to call when the layer leaves the DOM. */
export function attachSelectionHandling(layer: HTMLElement): () => void {
  const end = document.createElement("div");
  end.className = "endOfContent";
  layer.append(end);

  const onMouseDown = () => layer.classList.add("selecting");
  layer.addEventListener("mousedown", onMouseDown);
  layers.set(layer, end);
  enableGlobalListeners();

  return () => {
    layer.removeEventListener("mousedown", onMouseDown);
    layers.delete(layer);
    if (layers.size === 0) {
      globalListeners?.abort();
      globalListeners = null;
    }
  };
}

/** Whether the selection's end stayed put since `previous`, i.e. the reader is
 *  moving its start. A range whose nodes have left the DOM (a page recycled
 *  mid-selection) can't be compared; treat that as the end moving, the usual
 *  case. */
function movingStart(range: Range, previous: Range): boolean {
  try {
    return (
      range.compareBoundaryPoints(Range.END_TO_END, previous) === 0 ||
      range.compareBoundaryPoints(Range.START_TO_END, previous) === 0
    );
  } catch {
    return false;
  }
}

function enableGlobalListeners(): void {
  if (globalListeners) return;
  globalListeners = new AbortController();
  const { signal } = globalListeners;
  let pointerDown = false;
  let previousRange: Range | null = null;

  const resetAll = () => layers.forEach(reset);

  document.addEventListener(
    "pointerdown",
    () => {
      pointerDown = true;
    },
    { signal },
  );
  document.addEventListener(
    "pointerup",
    () => {
      pointerDown = false;
      resetAll();
    },
    { signal },
  );
  window.addEventListener(
    "blur",
    () => {
      pointerDown = false;
      resetAll();
    },
    { signal },
  );
  document.addEventListener(
    "keyup",
    () => {
      if (!pointerDown) resetAll();
    },
    { signal },
  );
  document.addEventListener(
    "selectionchange",
    () => {
      const selection = document.getSelection();
      if (!selection || selection.rangeCount === 0) {
        resetAll();
        return;
      }

      const active = new Set<HTMLElement>();
      for (let i = 0; i < selection.rangeCount; i++) {
        const range = selection.getRangeAt(i);
        for (const layer of layers.keys()) {
          if (!active.has(layer) && range.intersectsNode(layer)) {
            active.add(layer);
          }
        }
      }
      for (const [layer, end] of layers) {
        if (active.has(layer)) layer.classList.add("selecting");
        else reset(end, layer);
      }

      const range = selection.getRangeAt(0);
      const modifyStart =
        previousRange !== null && movingStart(range, previousRange);
      let anchor: Node | null = modifyStart
        ? range.startContainer
        : range.endContainer;
      if (anchor.nodeType === Node.TEXT_NODE) anchor = anchor.parentNode;
      if (anchor instanceof Element && anchor.classList.contains("highlight")) {
        anchor = anchor.parentNode;
      }
      if (!modifyStart && range.endOffset === 0) {
        do {
          while (anchor && !anchor.previousSibling) anchor = anchor.parentNode;
          anchor = anchor?.previousSibling ?? null;
        } while (anchor && !anchor.childNodes.length);
      }

      const parent = anchor?.parentElement ?? null;
      const layer = parent?.closest<HTMLElement>(".textLayer") ?? null;
      const end = layer ? layers.get(layer) : undefined;
      if (anchor && parent && layer && end) {
        end.style.width = layer.style.width;
        end.style.height = layer.style.height;
        end.style.userSelect = "text";
        parent.insertBefore(end, modifyStart ? anchor : anchor.nextSibling);
      }
      previousRange = range.cloneRange();
    },
    { signal },
  );
}
