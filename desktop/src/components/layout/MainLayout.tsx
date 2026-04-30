import { FileText, Sparkles } from "lucide-react";

import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { PdfViewer } from "@/components/pdf-viewer/PdfViewer";
import { useAppStore } from "@/lib/store";
import { useState } from "react";

export function MainLayout() {
  const originalPath = useAppStore((s) => s.originalPdfPath);
  const translatedPath = useAppStore((s) => s.translatedPdfPath);
  const chatOpen = useAppStore((s) => s.chatOpen);
  const ragEnabled = useAppStore((s) => s.ragEnabled);
  const [scrollToPage, setScrollToPage] = useState<number | undefined>();

  const showChat = chatOpen && ragEnabled;

  return (
    <ResizablePanelGroup orientation="vertical" className="flex-1">
      <ResizablePanel defaultSize={showChat ? 65 : 100} minSize={30}>
        <ResizablePanelGroup orientation="horizontal" className="h-full">
          <ResizablePanel defaultSize={50} minSize={20}>
            <PdfViewer
              filePath={originalPath}
              label="Original"
              scrollToPage={scrollToPage}
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
          <ResizableHandle />
          <ResizablePanel defaultSize={50} minSize={20}>
            <PdfViewer
              filePath={translatedPath}
              label="Translated"
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
        </ResizablePanelGroup>
      </ResizablePanel>

      {showChat && (
        <>
          <ResizableHandle />
          <ResizablePanel defaultSize={35} minSize={20} maxSize={70}>
            <ChatPanel
              documentPath={originalPath}
              onJumpToPage={(p) => setScrollToPage(p)}
            />
          </ResizablePanel>
        </>
      )}
    </ResizablePanelGroup>
  );
}
