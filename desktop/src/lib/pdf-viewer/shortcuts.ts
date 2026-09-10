/**
 * The workspace's keyboard shortcuts, as a pure key-event → action mapping.
 *
 * The listener that uses this has to `preventDefault()` whatever it handles.
 * WebView2 has its own accelerators for several of these keys (Ctrl+F opens its
 * find bar), and they only stand down for keys the page claims.
 */

export type ViewerShortcut =
  | "open"
  | "find"
  | "find-next"
  | "find-previous"
  | "zoom-in"
  | "zoom-out"
  | "zoom-reset";

/** The parts of a `KeyboardEvent` this looks at. */
export interface ShortcutKeyEvent {
  key: string;
  code: string;
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
  metaKey: boolean;
  isComposing?: boolean;
}

export function matchShortcut(e: ShortcutKeyEvent): ViewerShortcut | null {
  // Alt+… is AltGr on many layouts, and the Windows key belongs to the OS.
  if (e.isComposing || e.altKey || e.metaKey) return null;

  if (!e.ctrlKey) {
    if (e.key === "F3") return e.shiftKey ? "find-previous" : "find-next";
    return null;
  }

  // Letters go by `key`, not `code`: `code` is the physical key, which on a
  // non-QWERTY layout is a different letter.
  const letter = e.key.length === 1 ? e.key.toLowerCase() : "";
  if (letter === "o" && !e.shiftKey) return "open";
  if (letter === "f" && !e.shiftKey) return "find";

  // `+` is Shift+= on most layouts, so Shift is allowed for zooming in.
  if (e.key === "=" || e.key === "+" || e.code === "NumpadAdd") {
    return "zoom-in";
  }
  if (e.key === "-" || e.key === "_" || e.code === "NumpadSubtract") {
    return "zoom-out";
  }
  if ((e.key === "0" || e.code === "Numpad0") && !e.shiftKey) {
    return "zoom-reset";
  }
  return null;
}
