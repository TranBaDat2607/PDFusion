import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { toast } from "sonner";

import { AboutDialog } from "@/components/AboutDialog";
import { Header } from "@/components/layout/Header";
import { ContextBar } from "@/components/layout/ContextBar";
import { DropOverlay } from "@/components/layout/DropOverlay";
import { MainLayout } from "@/components/layout/MainLayout";
import { DiscardTranslationDialog } from "@/components/translation/DiscardTranslationDialog";
import {
  PageLimitDialog,
  type OverLimit,
} from "@/components/translation/PageLimitDialog";
import { ProgressOverlay } from "@/components/translation/ProgressOverlay";
import { SettingsSheet } from "@/components/settings/SettingsSheet";
import { SetupScreen } from "@/components/setup/SetupScreen";
import { StartupScreen } from "@/components/StartupScreen";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useConfig } from "@/hooks/useConfig";
import { useEngineSetup } from "@/hooks/useEngineSetup";
import { useFileDrop } from "@/hooks/useFileDrop";
import { useSidecar } from "@/hooks/useSidecar";
import { isTranslationBusy, useTranslation } from "@/hooks/useTranslation";
import { shouldConfirmSwap, type SwapPrompt } from "@/lib/document-swap";
import { readSkipped, shouldShowSetup } from "@/lib/engine-setup";
import {
  checkPageLimit,
  formatPageRanges,
  parsePageRanges,
} from "@/lib/page-range";
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

  return <EngineGate />;
}

/**
 * The second boot gate: the sidecar is up, but the ~290 MB of layout models,
 * fonts and language packs a translation needs may not be on disk yet.
 *
 * It sits between the sidecar gate and the workspace rather than inside the
 * workspace because it is also the mid-session destination for a Translate that
 * came back 409 — `engineSetupRequired` in the store. One screen, one place it
 * can be rendered from.
 */
function EngineGate() {
  const setup = useEngineSetup();
  const required = useAppStore((s) => s.engineSetupRequired);
  const setRequired = useAppStore((s) => s.setEngineSetupRequired);
  // Read once: this only changes through the Not now button below, which
  // updates both localStorage and this copy.
  const [skipped, setSkipped] = useState(readSkipped);

  const engineReady = setup.state.engine?.ready ?? false;
  // Evaluated here rather than left to the effect, so a completed install
  // doesn't render the setup screen for one more frame before the store catches
  // up.
  const forced = required && !engineReady;

  useEffect(() => {
    if (required && engineReady) setRequired(false);
  }, [required, engineReady, setRequired]);

  // The status probe is stat calls, so this is a frame or two — but it is still
  // "the app hasn't finished starting", and that already has a screen.
  if (setup.state.probing && !forced) {
    return <StartupScreen state={{ status: "starting" }} />;
  }

  if (shouldShowSetup(setup.state.engine, skipped, forced)) {
    return (
      <SetupScreen
        state={setup.state}
        forced={forced}
        onInstall={() => void setup.install()}
        onSkip={() => {
          setup.skip();
          setSkipped(true);
          setRequired(false);
        }}
      />
    );
  }

  return <Workspace />;
}

function Workspace() {
  const [settingsOpen, setSettingsOpen] = useState(false);
  // The provider card Settings → Models opens at; unset opens at the top.
  const [settingsProvider, setSettingsProvider] = useState<string | undefined>();
  const [aboutOpen, setAboutOpen] = useState(false);
  // A Translate click that covered more pages than one translation may, held
  // while the user answers the offer. `path` guards against a document opened
  // (dropped, say) while the dialog was up.
  const [limitPrompt, setLimitPrompt] = useState<{
    check: OverLimit;
    path: string;
    bypassCache: boolean;
  } | null>(null);
  // A document waiting to be opened while a translation runs, held until the
  // user answers the offer to discard that run (#44).
  const [swapPrompt, setSwapPrompt] = useState<SwapPrompt | null>(null);
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
            service: config.translation.model.provider,
          }
        : {},
    [config],
  );

  /** Open `path`, unconditionally. Everything that swaps documents goes
   *  through `openDocument` instead, which asks first when that would throw
   *  away a running translation. */
  const applyOpen = useCallback(
    (path: string) => {
      setOriginalPath(path);
      setTranslatedPath(null);
      // The saved copy belongs to the *previous* document — keeping it would
      // make the toolbar offer "Open" on an unrelated file.
      setExportedPath(null);
      setLimitPrompt(null);
      // Both prompts are about the document being replaced. The swap one can
      // still be up here: a run that finished while the dialog waited leaves
      // the next open free to go straight through, and its question with it.
      setSwapPrompt(null);
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
    },
    [setOriginalPath, setTranslatedPath, setExportedPath, translation, selection],
  );

  // A translation is a job on the sidecar, not a piece of React state: swapping
  // the document out from under one used to leave it running, billing an LLM
  // and streaming events for a file nobody is looking at, with its `job_id`
  // dropped so the user could no longer cancel it (#44). Asking here rather
  // than disabling the Change PDF button, because a dropped file and an "Open
  // with PDFusion" handoff reach this same function without touching a button.
  const openDocument = useCallback(
    (path: string) => {
      const busy = isTranslationBusy(translation.state);
      if (!shouldConfirmSwap(busy, path, originalPath)) {
        // Re-opening the document that is being translated keeps its run —
        // `applyOpen` would reset the overlay out from under it.
        if (!busy) applyOpen(path);
        return;
      }
      setSwapPrompt({
        incomingPath: path,
        currentPath: originalPath,
        progress: translation.state.progress,
      });
    },
    [translation.state, originalPath, applyOpen],
  );

  // A second forwarded file arriving while the dialog is up replaces the
  // pending prompt, so the answer applies to the document asked for last.
  const confirmSwap = useCallback(
    (prompt: SwapPrompt) => {
      setSwapPrompt(null);
      // Not awaited: everything that has to happen before the swap — aborting
      // the stream, dropping the job id, clearing the overlay — is synchronous
      // inside `abandon`, and only the cancel POST is left. Waiting on that
      // would leave the user staring at the old document if the sidecar is
      // wedged, for a reply nothing here reads.
      void translation.abandon();
      applyOpen(prompt.incomingPath);
    },
    [translation, applyOpen],
  );

  const handlePickFile = useCallback(async () => {
    try {
      const selected = await openDialog({
        multiple: false,
        directory: false,
        filters: [{ name: "PDF documents", extensions: ["pdf"] }],
      });
      if (typeof selected === "string") {
        openDocument(selected);
      }
    } catch (e) {
      toast.error("Could not open file picker", {
        description: (e as Error).message,
      });
    }
  }, [openDocument]);

  // A PDF dragged onto the window goes through the same handler as the picker.
  const fileDrop = useFileDrop(openDocument);

  // `openDocument` is rebuilt whenever the toolbar selection changes — and now
  // on every progress tick, since it reads the running translation's state —
  // but the listener below must be registered exactly once: re-running that
  // effect would re-open the command-line document each time. The ref keeps
  // the handler current without making it a dependency.
  const openDocumentRef = useRef(openDocument);
  openDocumentRef.current = openDocument;

  // A PDF named on the command line — `pdfusion.exe paper.pdf`, or a second
  // launch that the single-instance guard turned away and forwarded here
  // instead of starting another app. Both land on the same handler.
  useEffect(() => {
    let cancelled = false;
    let unlisten: (() => void) | undefined;

    void (async () => {
      try {
        // Subscribe *before* asking for our own argv. Tauri does not buffer
        // events, so anything emitted before this resolves is dropped — and
        // the gap is exactly when a second launch is most likely to arrive,
        // since the user is already double-clicking a PDF.
        const un = await listen<string>("pdfusion://open-file", (event) => {
          if (!cancelled) openDocumentRef.current(event.payload);
        });
        if (cancelled) un();
        else unlisten = un;

        const initial = await invoke<string | null>("initial_file_argument");
        if (!cancelled && initial) openDocumentRef.current(initial);
      } catch {
        // No shell (plain `pnpm dev` in a browser tab) — nothing to open.
      }
    })();

    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);

  // Translate, Re-translate and Retry all come through here. The Pages box is
  // read at click time, and a request over the page limit becomes an offer of
  // the pages that fit (#33) instead of a job the sidecar would refuse. With
  // the page count not known yet, the sidecar's own check is what applies.
  const requestTranslation = useCallback(
    (bypassCache: boolean) => {
      if (!originalPath) return;
      const { pageRangeText, originalPageCount } = useAppStore.getState();
      const parsed = parsePageRanges(pageRangeText, originalPageCount);
      if (!parsed.ok) {
        toast.error("Check the pages to translate", { description: parsed.error });
        return;
      }
      const maxPages = config?.translation.max_pages;
      if (originalPageCount != null && maxPages != null) {
        const check = checkPageLimit(originalPageCount, parsed.ranges, maxPages);
        if (check.over) {
          setLimitPrompt({ check, path: originalPath, bypassCache });
          return;
        }
      }
      void translation.start(originalPath, {
        ...selection,
        bypassCache,
        pageRanges: parsed.ranges,
      });
    },
    [originalPath, config, translation, selection],
  );

  const handleTranslate = useCallback(
    () => requestTranslation(false),
    [requestTranslation],
  );

  const handleReTranslate = useCallback(
    () => requestTranslation(true),
    [requestTranslation],
  );

  // The offer accepted: the Pages box shows what is being translated, so the
  // next click (and Re-translate) asks for the same pages.
  const confirmLimit = useCallback(
    (check: OverLimit) => {
      const prompt = limitPrompt;
      setLimitPrompt(null);
      if (!prompt || prompt.path !== originalPath) return;
      useAppStore
        .getState()
        .setPageRangeText(formatPageRanges(check.suggestion, "-"));
      void translation.start(prompt.path, {
        ...selection,
        bypassCache: prompt.bypassCache,
        pageRanges: check.suggestion,
      });
    },
    [limitPrompt, originalPath, translation, selection],
  );

  return (
    <div className="relative flex h-full w-full flex-col bg-background text-foreground">
      <Header
        onOpenSettings={() => {
          setSettingsProvider(undefined);
          setSettingsOpen(true);
        }}
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
        onOpenSettings={(provider) => {
          setSettingsProvider(provider);
          setSettingsOpen(true);
        }}
      />
      <div className="relative flex-1 overflow-hidden">
        <MainLayout onPickFile={handlePickFile} />
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

      <DropOverlay state={fileDrop} />
      <PageLimitDialog
        check={limitPrompt?.check ?? null}
        onCancel={() => setLimitPrompt(null)}
        onConfirm={confirmLimit}
      />
      <DiscardTranslationDialog
        prompt={swapPrompt}
        onCancel={() => setSwapPrompt(null)}
        onConfirm={confirmSwap}
      />
      <SettingsSheet
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        focusProvider={settingsProvider}
      />
      <AboutDialog open={aboutOpen} onOpenChange={setAboutOpen} />
    </div>
  );
}
