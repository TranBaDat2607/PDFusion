/**
 * App-wide client state. TanStack Query owns server state — this store is
 * only for ephemeral UI state.
 */

import { create } from "zustand";

export type Theme = "light" | "dark" | "system";

interface AppState {
  /** Path of the currently loaded original PDF (absolute, host filesystem). */
  originalPdfPath: string | null;
  setOriginalPdfPath: (path: string | null) => void;

  /** Dimensions of the original PDF's first page (CSS points, scale=1). Used
   *  to render a blank placeholder in the translated panel before a
   *  translation exists. */
  originalFirstPageSize: { width: number; height: number } | null;
  setOriginalFirstPageSize: (
    size: { width: number; height: number } | null,
  ) => void;

  /** Path of the translated PDF (filled when a translation completes). */
  translatedPdfPath: string | null;
  setTranslatedPdfPath: (path: string | null) => void;

  /** Monotonic counter bumped at the start of every translation. The
   *  translated-panel PdfViewer includes this in its load-effect deps so
   *  Re-translate (which writes to the SAME rolling path) forces a fresh
   *  fetch + parse instead of reusing the cached pdf.js document. */
  translatedReloadKey: number;
  bumpTranslatedReloadKey: () => void;

  /** Whether the chat drawer is expanded. */
  chatOpen: boolean;
  toggleChat: () => void;
  setChatOpen: (open: boolean) => void;

  /** RAG enabled toggle (mirrors the value in /config). */
  ragEnabled: boolean;
  setRagEnabled: (enabled: boolean) => void;

  /** Active translation job ID (null = idle). */
  activeTranslationJob: string | null;
  setActiveTranslationJob: (id: string | null) => void;

  /** Streaming-translate chunk progress. Set as chunks complete; reset on
   *  new translation start or when the job ends. */
  chunkProgress: {
    chunksReady: number;
    totalChunks: number;
    pagesReady: number;
  } | null;
  setChunkProgress: (p: AppState["chunkProgress"]) => void;

  /** 1-indexed page the user is currently looking at in the *original*
   *  viewer. Updated (throttled) by the IntersectionObserver in PdfViewer.
   *  Seeds the backend priority queue at translation start and drives
   *  live re-prioritization as the user scrolls. */
  visiblePage: number | null;
  setVisiblePage: (page: number | null) => void;
}

export const useAppStore = create<AppState>((set) => ({
  originalPdfPath: null,
  setOriginalPdfPath: (path) => set({ originalPdfPath: path }),

  originalFirstPageSize: null,
  setOriginalFirstPageSize: (size) => set({ originalFirstPageSize: size }),

  translatedPdfPath: null,
  setTranslatedPdfPath: (path) => set({ translatedPdfPath: path }),

  translatedReloadKey: 0,
  bumpTranslatedReloadKey: () =>
    set((s) => ({ translatedReloadKey: s.translatedReloadKey + 1 })),

  chatOpen: false,
  toggleChat: () => set((s) => ({ chatOpen: !s.chatOpen })),
  setChatOpen: (chatOpen) => set({ chatOpen }),

  ragEnabled: false,
  setRagEnabled: (ragEnabled) => set({ ragEnabled }),

  activeTranslationJob: null,
  setActiveTranslationJob: (activeTranslationJob) =>
    set({ activeTranslationJob }),

  chunkProgress: null,
  setChunkProgress: (chunkProgress) => set({ chunkProgress }),

  visiblePage: null,
  setVisiblePage: (visiblePage) => set({ visiblePage }),
}));
