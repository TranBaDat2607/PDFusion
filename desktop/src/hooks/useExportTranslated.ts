/**
 * Save / open / reveal actions for a finished (or partially finished)
 * translation.
 *
 * The pipeline's own output is temporary by design, so this hook is the only
 * path by which a user ends up with a copy they keep. It binds the real Tauri
 * + sidecar collaborators to the pure flow in `lib/export-pdf.ts`.
 */

import { useCallback, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { save as showSaveDialog } from "@tauri-apps/plugin-dialog";
import { toast } from "sonner";

import { api } from "@/lib/api-client";
import {
  formatBytes,
  runExport,
  type ExportedFile,
  type ExportOutcome,
} from "@/lib/export-pdf";
import { useAppStore } from "@/lib/store";

/** Hand a path to the OS shell, surfacing any refusal as a toast. The Rust
 *  side rejects non-PDFs and missing files with a message worth showing. */
async function shellAction(
  command: "open_path_in_default_app" | "reveal_path_in_file_manager",
  path: string,
  failure: string,
): Promise<void> {
  try {
    await invoke(command, { path });
  } catch (e) {
    toast.error(failure, { description: (e as Error).message ?? String(e) });
  }
}

const reveal = (path: string) =>
  shellAction("reveal_path_in_file_manager", path, "Could not open the folder");

export function useExportTranslated() {
  const translatedPath = useAppStore((s) => s.translatedPdfPath);
  const originalPath = useAppStore((s) => s.originalPdfPath);
  const exportedPath = useAppStore((s) => s.exportedPdfPath);
  const setExportedPath = useAppStore((s) => s.setExportedPdfPath);
  // The language the displayed run produced, not the live config value — see
  // `translationTargetLang` in the store.
  const targetLang = useAppStore((s) => s.translationTargetLang);
  const [saving, setSaving] = useState(false);

  /**
   * Prefer a path the user owns. Falls back to the temp artifact, which is
   * still valid for the lifetime of the app session.
   */
  const actionablePath = exportedPath ?? translatedPath;

  const saveAs = useCallback(async (): Promise<ExportOutcome> => {
    setSaving(true);
    try {
      const outcome = await runExport(
        { sourcePath: translatedPath, originalPath, targetLang },
        {
          showSaveDialog,
          exportPdf: (source, destination, protectPath) =>
            api.post<ExportedFile>("/pdf/export", {
              source_path: source,
              destination_path: destination,
              protect_path: protectPath,
            }),
        },
      );

      if (outcome.status === "saved") {
        setExportedPath(outcome.path);
        toast.success("Translated PDF saved", {
          description: `${outcome.path} · ${formatBytes(outcome.bytes)}`,
          action: {
            label: "Show in folder",
            // Not `revealFile`: that closes over the pre-save `actionablePath`.
            onClick: () => void reveal(outcome.path),
          },
        });
      } else if (outcome.status === "error") {
        toast.error("Could not save the translated PDF", {
          description: outcome.message,
        });
      }
      // `cancelled` is a normal user choice — deliberately silent.
      return outcome;
    } finally {
      setSaving(false);
    }
  }, [translatedPath, originalPath, targetLang, setExportedPath]);

  const openFile = useCallback(async () => {
    if (!actionablePath) return;
    await shellAction(
      "open_path_in_default_app",
      actionablePath,
      "Could not open the PDF",
    );
  }, [actionablePath]);

  const revealFile = useCallback(async () => {
    if (!actionablePath) return;
    await reveal(actionablePath);
  }, [actionablePath]);

  return {
    /** True while the Save dialog is open or the copy is in flight. */
    saving,
    /** Whether there is anything to save/open at all. */
    hasTranslation: !!translatedPath,
    /** Permanent path, once the user has saved. */
    exportedPath,
    saveAs,
    openFile,
    revealFile,
  };
}
