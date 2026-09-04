import { useCallback, useMemo, useState } from "react";
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
import { useConfig } from "@/hooks/useConfig";
import { useSidecar } from "@/hooks/useSidecar";
import { isTranslationBusy, useTranslation } from "@/hooks/useTranslation";
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
  const setExportedPath = useAppStore((s) => s.setExportedPdfPath);
  const originalPath = useAppStore((s) => s.originalPdfPath);
  const translation = useTranslation();
  const { data: config } = useConfig();

  // The toolbar persists its dropdowns to config, but that PUT is async — a
  // Translate click can land first. Read the selection here and send it with
  // the request so the run can't use a stale one.
  // Deliberately the *requested* service, not the effective one: the sidecar
  // does its own no-key fallback and emits the "falling back to Argos" notice
  // the user sees as a toast. Pre-resolving it here would silence that.
  const selection = useMemo(
    () =>
      config
        ? {
            sourceLang: config.translation.default_source_lang,
            targetLang: config.translation.default_target_lang,
            service: config.translation.preferred_service,
          }
        : {},
    [config],
  );

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
        // The saved copy belongs to the *previous* document — keeping it would
        // make the toolbar offer "Open" on an unrelated file.
        setExportedPath(null);
        translation.reset();
        // Fire-and-forget pre-warm: by the time the user clicks Translate, the
        // Argos pack should be installed (or the LLM client should be live).
        // Carries the current selection — an empty body warms the configured
        // default, which is the wrong backend once the user has changed the
        // dropdowns. Errors are intentionally swallowed: this is a UX
        // optimization, never a correctness gate.
        void api
          .post("/translate/prewarm", {
            source_lang: selection.sourceLang,
            target_lang: selection.targetLang,
            service: selection.service,
          })
          .catch(() => undefined);
      }
    } catch (e) {
      toast.error("Could not open file picker", {
        description: (e as Error).message,
      });
    }
  }, [setOriginalPath, setTranslatedPath, setExportedPath, translation, selection]);

  const handleTranslate = useCallback(() => {
    if (!originalPath) return;
    void translation.start(originalPath, selection);
  }, [originalPath, translation, selection]);

  const handleReTranslate = useCallback(() => {
    if (!originalPath) return;
    void translation.start(originalPath, { ...selection, bypassCache: true });
  }, [originalPath, translation, selection]);

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
        translating={isTranslationBusy(translation.state)}
        // Re-translate is available whenever a PDF is loaded and we're not
        // currently running — including from idle (just-opened previously-
        // translated file), error, or cancelled states. The previous
        // status==="done" gate forced users through a (potentially stale)
        // cache hit before they could force-fresh.
        canReTranslate={
          !!originalPath && !isTranslationBusy(translation.state)
        }
      />
      <div className="relative flex-1 overflow-hidden">
        <MainLayout />
        <ProgressOverlay
          state={translation.state}
          onCancel={translation.cancel}
          onDismiss={translation.reset}
          // Same path as the toolbar's Re-translate: a retry must skip the
          // PDF cache, or a cached earlier result would be served instead of
          // the re-run the user asked for.
          onRetry={handleReTranslate}
        />
      </div>

      <SettingsSheet open={settingsOpen} onOpenChange={setSettingsOpen} />
      <AboutDialog open={aboutOpen} onOpenChange={setAboutOpen} />
    </div>
  );
}
