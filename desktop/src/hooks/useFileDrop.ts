import { useEffect, useRef, useState } from "react";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { toast } from "sonner";

import { firstPdfPath } from "@/lib/file-drop";

export type FileDropState =
  | { kind: "idle" }
  | { kind: "pdf"; path: string }
  | { kind: "unsupported" };

const IDLE: FileDropState = { kind: "idle" };

/**
 * Open a PDF dragged onto the window.
 *
 * Files dropped on a Tauri window never reach the page as HTML5 drag events.
 * With `dragDropEnabled` (the default) the webview hands them to the shell,
 * which reports real paths through `onDragDropEvent`. That's also why nothing
 * in this app can use HTML5 drag and drop.
 *
 * Returns what's being dragged, for the overlay. Only `enter` carries paths
 * (`over` has just a position), so that's where the verdict is made.
 */
export function useFileDrop(onOpen: (path: string) => void): FileDropState {
  const [state, setState] = useState<FileDropState>(IDLE);
  // Subscribed once; the ref keeps the handler current. Same shape as the
  // `pdfusion://open-file` listener in App.tsx.
  const onOpenRef = useRef(onOpen);
  onOpenRef.current = onOpen;

  useEffect(() => {
    let cancelled = false;
    let unlisten: (() => void) | undefined;

    void (async () => {
      try {
        const un = await getCurrentWebview().onDragDropEvent(({ payload }) => {
          if (cancelled) return;
          if (payload.type === "enter") {
            // A drag with no files (text dragged out of the page itself) isn't
            // an attempt to open anything.
            if (payload.paths.length === 0) return;
            const path = firstPdfPath(payload.paths);
            setState(path ? { kind: "pdf", path } : { kind: "unsupported" });
          } else if (payload.type === "leave") {
            setState(IDLE);
          } else if (payload.type === "drop") {
            setState(IDLE);
            if (payload.paths.length === 0) return;
            const path = firstPdfPath(payload.paths);
            if (path) {
              onOpenRef.current(path);
            } else {
              toast.error("Only PDF files can be opened", {
                description: "Drop a .pdf file onto the window to open it.",
              });
            }
          }
        });
        if (cancelled) un();
        else unlisten = un;
      } catch {
        // No shell (plain `pnpm dev` in a browser tab) — nothing to listen to.
      }
    })();

    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);

  return state;
}
