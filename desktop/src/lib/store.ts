/**
 * App-wide client state. TanStack Query owns server state — this store is
 * only for ephemeral UI state.
 */

import { create } from "zustand";

import {
  appendArtifactChange,
  type ArtifactChange,
} from "@/lib/pdf-viewer/artifact-swap";
import type { ChunkProgress } from "@/lib/translation-progress";

export type Theme = "light" | "dark" | "system";

interface AppState {
  /** Path of the currently loaded original PDF (absolute, host filesystem).
   *  Setting a different one also clears `originalPageCount` and
   *  `pageRangeText`, which describe the previous document. */
  originalPdfPath: string | null;
  setOriginalPdfPath: (path: string | null) => void;

  /** Pages in the original PDF, reported by its viewer once pdf.js has loaded
   *  it; `null` until then. */
  originalPageCount: number | null;
  setOriginalPageCount: (count: number | null) => void;

  /** The toolbar's Pages box, as typed (#33). Blank is the whole document.
   *  Parsed by `lib/page-range.ts` wherever it's read. */
  pageRangeText: string;
  setPageRangeText: (text: string) => void;

  /** Dimensions of the original PDF's first page (CSS points, scale=1). Used
   *  to render a blank placeholder in the translated panel before a
   *  translation exists. */
  originalFirstPageSize: { width: number; height: number } | null;
  setOriginalFirstPageSize: (
    size: { width: number; height: number } | null,
  ) => void;

  /** Path of the translated PDF (filled when a translation completes).
   *  NOTE: this is an *ephemeral* artifact — a rolling file in the per-job
   *  `%TEMP%\pdfusion-translate-*` dir, wiped by the next job and on app exit.
   *  Never present it to the user as a saved copy; see `exportedPdfPath`. */
  translatedPdfPath: string | null;
  setTranslatedPdfPath: (path: string | null) => void;

  /** Where the user actually saved the translation via the Save dialog, if
   *  they have. This is the only path that survives the app closing, so it's
   *  the only one the UI may call "Saved to". Cleared on a new translation
   *  run and when a different document is opened. */
  exportedPdfPath: string | null;
  setExportedPdfPath: (path: string | null) => void;

  /** Take a freshly produced artifact as the on-screen translation, retiring
   *  any saved copy — which describes the *previous* result from here on.
   *  A single action rather than two setters: this fires once per
   *  `chunk_ready` (i.e. per page), and the two writes must not drift apart.
   *
   *  `changedPages` is how the new artifact differs from the one it replaces
   *  (`chunk_ready.pages_in_chunk`, `[first, last]`, 1-indexed). It goes into
   *  `translatedChanges`; omitted means unknown. */
  adoptTranslatedArtifact: (
    path: string,
    changedPages?: [number, number] | null,
  ) => void;

  /** Every artifact adopted, newest last, trimmed to the last few dozen. The
   *  translated viewer folds in the entries it hasn't seen yet so it repaints
   *  only the pages that changed. It's a log rather than "the latest change"
   *  because a single render can deliver several `chunk_ready`s; see
   *  `lib/pdf-viewer/artifact-swap.ts`. Re-adopting the path already on screen
   *  adds nothing, because the file didn't change. */
  translatedChanges: ArtifactChange[];

  /** Language the on-screen translation was actually produced in, as reported
   *  by the job that produced it — not read from live config, which the user
   *  can change after a run. Names the default file in the Save dialog. */
  translationTargetLang: string;
  setTranslationTargetLang: (lang: string) => void;

  /** Monotonic counter bumped at the start of every translation. The
   *  translated-panel PdfViewer includes this in its load-effect deps so
   *  Re-translate (which writes to the SAME rolling path) forces a fresh
   *  fetch + parse instead of reusing the cached pdf.js document. */
  translatedReloadKey: number;
  bumpTranslatedReloadKey: () => void;

  /** Whether the chat panel is showing. Showing and hiding is all this does:
   *  whether chat is on at all is `config.rag.chat_enabled` (`useChatEnabled`),
   *  which is server state. */
  chatOpen: boolean;
  toggleChat: () => void;
  setChatOpen: (open: boolean) => void;

  /** Active translation job ID (null = idle). */
  activeTranslationJob: string | null;
  setActiveTranslationJob: (id: string | null) => void;

  /** A translate was refused with 409 because the offline engine's assets
   *  aren't installed. Re-opens the first-run setup screen over the
   *  workspace — the overlay can't resolve that, and the setup screen can. */
  engineSetupRequired: boolean;
  setEngineSetupRequired: (required: boolean) => void;

  /** Streaming-translate chunk progress. Set as chunks complete; reset on
   *  new translation start or when the job ends. Accumulated by
   *  `lib/translation-progress.ts` — chunks land in priority order, so this
   *  records *which* completed, never a high-water mark. */
  chunkProgress: ChunkProgress | null;
  setChunkProgress: (p: ChunkProgress | null) => void;

  /** 1-indexed page the user is currently looking at in the *original*
   *  viewer. Reported by PdfViewer's scroll handling whenever that page
   *  changes. Seeds the backend priority queue at translation start and drives
   *  live re-prioritization as the user scrolls. */
  visiblePage: number | null;
  setVisiblePage: (page: number | null) => void;
}

export const useAppStore = create<AppState>((set) => ({
  originalPdfPath: null,
  setOriginalPdfPath: (path) =>
    set((s) =>
      path === s.originalPdfPath
        ? {}
        : { originalPdfPath: path, originalPageCount: null, pageRangeText: "" },
    ),

  originalPageCount: null,
  setOriginalPageCount: (originalPageCount) => set({ originalPageCount }),

  pageRangeText: "",
  setPageRangeText: (pageRangeText) => set({ pageRangeText }),

  originalFirstPageSize: null,
  setOriginalFirstPageSize: (size) => set({ originalFirstPageSize: size }),

  translatedPdfPath: null,
  setTranslatedPdfPath: (path) => set({ translatedPdfPath: path }),

  exportedPdfPath: null,
  setExportedPdfPath: (path) => set({ exportedPdfPath: path }),

  adoptTranslatedArtifact: (path, changedPages) =>
    set((s) => ({
      translatedPdfPath: path,
      exportedPdfPath: null,
      translatedChanges:
        path === s.translatedPdfPath
          ? s.translatedChanges
          : appendArtifactChange(s.translatedChanges, changedPages ?? null),
    })),

  translatedChanges: [],

  translationTargetLang: "vi",
  setTranslationTargetLang: (translationTargetLang) =>
    set({ translationTargetLang }),

  translatedReloadKey: 0,
  bumpTranslatedReloadKey: () =>
    set((s) => ({ translatedReloadKey: s.translatedReloadKey + 1 })),

  chatOpen: false,
  toggleChat: () => set((s) => ({ chatOpen: !s.chatOpen })),
  setChatOpen: (chatOpen) => set({ chatOpen }),

  activeTranslationJob: null,
  setActiveTranslationJob: (activeTranslationJob) =>
    set({ activeTranslationJob }),

  engineSetupRequired: false,
  setEngineSetupRequired: (engineSetupRequired) => set({ engineSetupRequired }),

  chunkProgress: null,
  setChunkProgress: (chunkProgress) => set({ chunkProgress }),

  visiblePage: null,
  setVisiblePage: (visiblePage) => set({ visiblePage }),
}));
