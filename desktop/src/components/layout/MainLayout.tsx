import { FileText, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { GroupImperativeHandle, Layout } from "react-resizable-panels";

import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { ChatPanel } from "@/components/chat/ChatPanel";
import {
  PdfViewer,
  type PdfViewerHandle,
} from "@/components/pdf-viewer/PdfViewer";
import { useChatEnabled } from "@/hooks/useConfig";
import { matchShortcut } from "@/lib/pdf-viewer/shortcuts";
import { useAppStore } from "@/lib/store";
import { cn } from "@/lib/utils";

const ANIM_MS = 220;

type Pane = "original" | "translated";

interface MainLayoutProps {
  /** Ctrl+O. The picker itself lives in `App.tsx` with the rest of the
   *  open-document flow. */
  onPickFile: () => void;
}

export function MainLayout({ onPickFile }: MainLayoutProps) {
  const originalPath = useAppStore((s) => s.originalPdfPath);
  const translatedPath = useAppStore((s) => s.translatedPdfPath);
  const translatedReloadKey = useAppStore((s) => s.translatedReloadKey);
  const translatedChanges = useAppStore((s) => s.translatedChanges);
  const originalFirstPageSize = useAppStore((s) => s.originalFirstPageSize);
  const setOriginalFirstPageSize = useAppStore(
    (s) => s.setOriginalFirstPageSize,
  );
  const setVisiblePage = useAppStore((s) => s.setVisiblePage);
  const chatOpen = useAppStore((s) => s.chatOpen);
  const chatEnabled = useChatEnabled();
  const [scrollToPage, setScrollToPage] = useState<number | undefined>();

  // Turning chat off in Settings unmounts the panel, so nothing is indexed.
  const showChat = chatOpen && chatEnabled;
  const groupRef = useRef<GroupImperativeHandle | null>(null);
  const [animating, setAnimating] = useState(false);
  const [chatPanelMounted, setChatPanelMounted] = useState(false);

  // The pane the zoom and find shortcuts go to: the one last clicked or
  // focused. The panes scroll and zoom independently, so a shortcut has to pick
  // one of them.
  const [activePane, setActivePane] = useState<Pane>("original");
  const activePaneRef = useRef(activePane);
  activePaneRef.current = activePane;
  const originalViewer = useRef<PdfViewerHandle>(null);
  const translatedViewer = useRef<PdfViewerHandle>(null);
  const onPickFileRef = useRef(onPickFile);
  onPickFileRef.current = onPickFile;

  // Mount chat panel immediately on open; keep it mounted briefly after close
  // so the chat content's exit animation can play.
  useEffect(() => {
    if (showChat) {
      setChatPanelMounted(true);
      return;
    }
    const t = setTimeout(() => setChatPanelMounted(false), ANIM_MS);
    return () => clearTimeout(t);
  }, [showChat]);

  // Redistribute panels to equal sizes on every panel-count change.
  // While the chat panel is exiting (mounted=true, showChat=false) we shrink
  // it to 0% so the column collapses in lockstep with the content fade-out.
  useEffect(() => {
    setAnimating(true);
    let cancelled = false;
    let rafId = 0;

    const expectedCount = chatPanelMounted ? 3 : 2;
    const tryApply = () => {
      if (cancelled || !groupRef.current) return;
      const current = groupRef.current.getLayout();
      const ids = Object.keys(current);
      if (ids.length !== expectedCount) {
        rafId = requestAnimationFrame(tryApply);
        return;
      }
      let layout: Layout;
      if (chatPanelMounted && !showChat) {
        layout = { original: 50, translated: 50, chat: 0 };
      } else {
        const equal = 100 / ids.length;
        layout = Object.fromEntries(ids.map((id) => [id, equal]));
      }
      groupRef.current.setLayout(layout);
    };
    rafId = requestAnimationFrame(tryApply);

    const t = setTimeout(() => setAnimating(false), ANIM_MS + 32);
    return () => {
      cancelled = true;
      cancelAnimationFrame(rafId);
      clearTimeout(t);
    };
  }, [showChat, chatPanelMounted]);

  // The workspace's keyboard shortcuts. Capture phase, so a focused control
  // that stops propagation can't swallow them. Every handled key is claimed
  // with `preventDefault`, even with no document to act on: WebView2 has its
  // own find bar on Ctrl+F, and it only stands down for keys the page takes.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const action = matchShortcut(event);
      if (!action) return;
      // A dialog or the settings sheet owns the keyboard while it's open.
      if (document.querySelector('[role="dialog"][data-state="open"]')) return;
      event.preventDefault();

      if (action === "open") {
        onPickFileRef.current();
        return;
      }
      const [preferred, other] =
        activePaneRef.current === "original"
          ? [originalViewer.current, translatedViewer.current]
          : [translatedViewer.current, originalViewer.current];
      const viewer = preferred?.hasDocument()
        ? preferred
        : other?.hasDocument()
          ? other
          : null;
      if (!viewer) return;
      switch (action) {
        case "find":
          viewer.openFind();
          break;
        case "find-next":
          viewer.findNext();
          break;
        case "find-previous":
          viewer.findPrevious();
          break;
        case "zoom-in":
          viewer.zoomIn();
          break;
        case "zoom-out":
          viewer.zoomOut();
          break;
        case "zoom-reset":
          viewer.resetZoom();
          break;
      }
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, []);

  return (
    <ResizablePanelGroup
      orientation="horizontal"
      groupRef={groupRef}
      className={cn(
        "flex-1",
        animating &&
          "[&_[data-panel]]:transition-[flex-grow] [&_[data-panel]]:duration-200 [&_[data-panel]]:ease-out",
      )}
    >
      <ResizablePanel id="original">
        <div
          className="h-full"
          onPointerDownCapture={() => setActivePane("original")}
          onFocusCapture={() => setActivePane("original")}
        >
          <PdfViewer
            ref={originalViewer}
            filePath={originalPath}
            label="Original"
            active={activePane === "original"}
            scrollToPage={scrollToPage}
            onFirstPageSize={setOriginalFirstPageSize}
            onVisiblePageChange={setVisiblePage}
            emptyState={
              <div className="flex flex-col items-center gap-2 text-center">
                <div className="rounded-full bg-muted p-3">
                  <FileText className="h-5 w-5 text-muted-foreground" />
                </div>
                <p className="text-sm font-medium">No document loaded</p>
                <p className="text-xs text-muted-foreground max-w-[220px]">
                  Click "Open PDF" in the toolbar above, or drop a PDF onto
                  the window.
                </p>
              </div>
            }
          />
        </div>
      </ResizablePanel>
      <ResizableHandle withHandle />
      <ResizablePanel id="translated">
        <div
          className="h-full"
          onPointerDownCapture={() => setActivePane("translated")}
          onFocusCapture={() => setActivePane("translated")}
        >
          <PdfViewer
            ref={translatedViewer}
            filePath={translatedPath}
            label="Translated"
            active={activePane === "translated"}
            reloadKey={translatedReloadKey}
            incrementalUpdates
            changeLog={translatedChanges}
            placeholderSize={originalPath ? originalFirstPageSize : null}
            emptyState={
              <div className="flex flex-col items-center gap-2 text-center">
                <div className="rounded-full bg-muted p-3">
                  <Sparkles className="h-5 w-5 text-muted-foreground" />
                </div>
                <p className="text-sm font-medium">No translation yet</p>
                <p className="text-xs text-muted-foreground max-w-[220px]">
                  {originalPath
                    ? 'Click "Translate" to start.'
                    : "Open a PDF first."}
                </p>
              </div>
            }
          />
        </div>
      </ResizablePanel>

      {chatPanelMounted && (
        <>
          <ResizableHandle withHandle />
          <ResizablePanel id="chat">
            <ChatPanel
              documentPath={originalPath}
              onJumpToPage={(p) => setScrollToPage(p)}
              showing={showChat}
            />
          </ResizablePanel>
        </>
      )}
    </ResizablePanelGroup>
  );
}
