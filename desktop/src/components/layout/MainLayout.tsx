import { FileText, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { GroupImperativeHandle, Layout } from "react-resizable-panels";

import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { PdfViewer } from "@/components/pdf-viewer/PdfViewer";
import { useAppStore } from "@/lib/store";
import { cn } from "@/lib/utils";

const ANIM_MS = 220;

export function MainLayout() {
  const originalPath = useAppStore((s) => s.originalPdfPath);
  const translatedPath = useAppStore((s) => s.translatedPdfPath);
  const translatedReloadKey = useAppStore((s) => s.translatedReloadKey);
  const originalFirstPageSize = useAppStore((s) => s.originalFirstPageSize);
  const setOriginalFirstPageSize = useAppStore(
    (s) => s.setOriginalFirstPageSize,
  );
  const setVisiblePage = useAppStore((s) => s.setVisiblePage);
  const chatOpen = useAppStore((s) => s.chatOpen);
  const ragEnabled = useAppStore((s) => s.ragEnabled);
  const [scrollToPage, setScrollToPage] = useState<number | undefined>();

  const showChat = chatOpen && ragEnabled;
  const groupRef = useRef<GroupImperativeHandle | null>(null);
  const [animating, setAnimating] = useState(false);
  const [chatPanelMounted, setChatPanelMounted] = useState(false);

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
        <PdfViewer
          filePath={originalPath}
          label="Original"
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
                Click "Open PDF" in the toolbar above to begin.
              </p>
            </div>
          }
        />
      </ResizablePanel>
      <ResizableHandle withHandle />
      <ResizablePanel id="translated">
        <PdfViewer
          filePath={translatedPath}
          label="Translated"
          reloadKey={translatedReloadKey}
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
