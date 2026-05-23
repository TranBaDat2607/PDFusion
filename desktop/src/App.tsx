import { useCallback, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { toast } from "sonner";

import { AboutDialog } from "@/components/AboutDialog";
import { Header } from "@/components/layout/Header";
import { ContextBar } from "@/components/layout/ContextBar";
import { MainLayout } from "@/components/layout/MainLayout";
import { ProgressOverlay } from "@/components/translation/ProgressOverlay";
import { SettingsSheet } from "@/components/settings/SettingsSheet";
import { StartupScreen } from "@/components/StartupScreen";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useSidecar } from "@/hooks/useSidecar";
import { useTranslation } from "@/hooks/useTranslation";
import { useAppStore } from "@/lib/store";
import { api } from "@/lib/api-client";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

export default function App() {
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <Shell />
          <Toaster richColors position="top-right" />
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}

function Shell() {
  const sidecar = useSidecar();

  if (sidecar.status !== "ready") {
    return <StartupScreen state={sidecar} />;
  }

  return <Workspace />;
}

function Workspace() {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [aboutOpen, setAboutOpen] = useState(false);
  const setOriginalPath = useAppStore((s) => s.setOriginalPdfPath);
  const setTranslatedPath = useAppStore((s) => s.setTranslatedPdfPath);
  const originalPath = useAppStore((s) => s.originalPdfPath);
  const translation = useTranslation();

  const handlePickFile = useCallback(async () => {
    try {
      const selected = await openDialog({
        multiple: false,
        directory: false,
        filters: [{ name: "PDF documents", extensions: ["pdf"] }],
      });
      if (typeof selected === "string") {
        setOriginalPath(selected);
        setTranslatedPath(null);
        translation.reset();
        // Fire-and-forget pre-warm: by the time the user clicks Translate, the
        // Argos pack should be installed (or the LLM client should be live).
        // Errors are intentionally swallowed — this is a UX optimization,
        // never a correctness gate.
        void api.post("/translate/prewarm", {}).catch(() => undefined);
      }
    } catch (e) {
      toast.error("Could not open file picker", {
        description: (e as Error).message,
      });
    }
  }, [setOriginalPath, setTranslatedPath, translation]);

  const handleTranslate = useCallback(() => {
    if (!originalPath) return;
    void translation.start(originalPath);
  }, [originalPath, translation]);

  const handleReTranslate = useCallback(() => {
    if (!originalPath) return;
    void translation.start(originalPath, { bypassCache: true });
  }, [originalPath, translation]);

  return (
    <div className="flex h-full w-full flex-col bg-background text-foreground">
      <Header
        onOpenSettings={() => setSettingsOpen(true)}
        onOpenAbout={() => setAboutOpen(true)}
      />
      <ContextBar
        onPickFile={handlePickFile}
        onTranslate={handleTranslate}
        onReTranslate={handleReTranslate}
        translating={translation.state.status === "running"}
        // Re-translate is available whenever a PDF is loaded and we're not
        // currently running — including from idle (just-opened previously-
        // translated file), error, or cancelled states. The previous
        // status==="done" gate forced users through a (potentially stale)
        // cache hit before they could force-fresh.
        canReTranslate={
          !!originalPath && translation.state.status !== "running"
        }
      />
      <div className="relative flex-1 overflow-hidden">
        <MainLayout />
        <ProgressOverlay
          state={translation.state}
          onCancel={translation.cancel}
          onDismiss={translation.reset}
        />
      </div>

      <SettingsSheet open={settingsOpen} onOpenChange={setSettingsOpen} />
      <AboutDialog open={aboutOpen} onOpenChange={setAboutOpen} />
    </div>
  );
}
