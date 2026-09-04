import { describe, expect, it, vi } from "vitest";

import {
  basename,
  dirname,
  formatBytes,
  runExport,
  stripExtension,
  suggestedExportName,
  suggestedExportPath,
  type ExportDeps,
  type ExportedFile,
} from "./export-pdf";

const TEMP_ARTIFACT =
  "C:\\Users\\Admin\\AppData\\Local\\Temp\\pdfusion-translate-a1b2\\paper_translated_v003.pdf";
const ORIGINAL = "D:\\Papers\\attention is all you need.pdf";

function deps(overrides: Partial<ExportDeps> = {}): ExportDeps {
  return {
    showSaveDialog: vi.fn(async () => "D:\\Papers\\attention_vi.pdf"),
    exportPdf: vi.fn(
      async (
        _source: string,
        destination: string,
        _protectPath: string | null,
      ): Promise<ExportedFile> => ({
        saved_path: destination,
        bytes_written: 2048,
      }),
    ),
    ...overrides,
  };
}

describe("path helpers", () => {
  it("splits Windows paths", () => {
    expect(basename("C:\\a\\b\\paper.pdf")).toBe("paper.pdf");
    expect(dirname("C:\\a\\b\\paper.pdf")).toBe("C:\\a\\b");
  });

  it("splits POSIX paths", () => {
    expect(basename("/home/me/paper.pdf")).toBe("paper.pdf");
    expect(dirname("/home/me/paper.pdf")).toBe("/home/me");
  });

  it("keeps the root separator for files at the root", () => {
    expect(dirname("/paper.pdf")).toBe("/");
  });

  it("returns an empty dirname for a bare filename", () => {
    expect(dirname("paper.pdf")).toBe("");
  });

  it("strips only the final extension", () => {
    expect(stripExtension("paper.tar.pdf")).toBe("paper.tar");
    expect(stripExtension("paper")).toBe("paper");
  });

  it("treats a leading dot as part of the name", () => {
    expect(stripExtension(".hidden")).toBe(".hidden");
  });
});

describe("suggestedExportName", () => {
  it("appends the target language to the original stem", () => {
    expect(suggestedExportName(ORIGINAL, "vi")).toBe(
      "attention is all you need_vi.pdf",
    );
  });

  it("follows the configured target language, not a hardcoded 'vi'", () => {
    expect(suggestedExportName("C:\\a\\paper.pdf", "ja")).toBe("paper_ja.pdf");
  });

  // Always appending is what keeps the suggestion from ever colliding with
  // the source document — see the note on `suggestedExportName`.
  it("appends even when the stem already ends in the suffix", () => {
    expect(suggestedExportName("C:\\a\\paper_vi.pdf", "vi")).toBe(
      "paper_vi_vi.pdf",
    );
  });

  it("falls back to a usable name when the path has no stem", () => {
    expect(suggestedExportName("", "vi")).toBe("translated_vi.pdf");
  });

  it("defaults the language to vi", () => {
    expect(suggestedExportName("C:\\a\\paper.pdf", "")).toBe("paper_vi.pdf");
  });
});

describe("suggestedExportPath", () => {
  it("puts the default file next to the source document", () => {
    expect(suggestedExportPath(ORIGINAL, "vi")).toBe(
      "D:\\Papers\\attention is all you need_vi.pdf",
    );
  });

  it("uses the source's own separator style", () => {
    expect(suggestedExportPath("/home/me/paper.pdf", "vi")).toBe(
      "/home/me/paper_vi.pdf",
    );
  });

  // Data-loss guard: pre-filling the dialog with the user's own document
  // would leave them one "Replace?" from destroying it. Appending the suffix
  // unconditionally makes that impossible by construction, including for a
  // source whose name only *looks* already-translated (Roman-numeral chapters).
  it("never proposes the source document itself", () => {
    for (const source of [
      "D:\\Papers\\chapter_vi.pdf",
      "D:\\Papers\\CHAPTER_VI.pdf",
      "/home/me/paper_vi.pdf",
    ]) {
      expect(suggestedExportPath(source, "vi")).not.toBe(source);
    }
  });
});

describe("runExport", () => {
  const params = {
    sourcePath: TEMP_ARTIFACT,
    originalPath: ORIGINAL,
    targetLang: "vi",
  };

  it("saves to the path the user picked", async () => {
    const d = deps();

    const outcome = await runExport(params, d);

    expect(outcome).toEqual({
      status: "saved",
      path: "D:\\Papers\\attention_vi.pdf",
      bytes: 2048,
    });
    expect(d.exportPdf).toHaveBeenCalledWith(
      TEMP_ARTIFACT,
      "D:\\Papers\\attention_vi.pdf",
      ORIGINAL,
    );
  });

  it("tells the sidecar which file must not be overwritten", async () => {
    const d = deps({
      showSaveDialog: vi.fn(async () => ORIGINAL),
    });

    await runExport(params, d);

    // The sidecar is the one that refuses; the frontend's job is to name the
    // document so it can. See `file_export.export_pdf(protect=...)`.
    expect(d.exportPdf).toHaveBeenCalledWith(TEMP_ARTIFACT, ORIGINAL, ORIGINAL);
  });

  it("offers <original>_vi.pdf beside the source as the dialog default", async () => {
    const d = deps();

    await runExport(params, d);

    expect(d.showSaveDialog).toHaveBeenCalledWith(
      expect.objectContaining({
        defaultPath: "D:\\Papers\\attention is all you need_vi.pdf",
        filters: [{ name: "PDF document", extensions: ["pdf"] }],
      }),
    );
  });

  it("treats a cancelled dialog as a normal outcome, not an error", async () => {
    const d = deps({ showSaveDialog: vi.fn(async () => null) });

    const outcome = await runExport(params, d);

    expect(outcome).toEqual({ status: "cancelled" });
    expect(d.exportPdf).not.toHaveBeenCalled();
  });

  it("adds a .pdf suffix when the user types a bare filename", async () => {
    const d = deps({
      showSaveDialog: vi.fn(async () => "D:\\Papers\\attention_vi"),
    });

    await runExport(params, d);

    expect(d.exportPdf).toHaveBeenCalledWith(
      TEMP_ARTIFACT,
      "D:\\Papers\\attention_vi.pdf",
      ORIGINAL,
    );
  });

  it("keeps an existing .pdf suffix regardless of case", async () => {
    const d = deps({
      showSaveDialog: vi.fn(async () => "D:\\Papers\\ATTENTION_VI.PDF"),
    });

    await runExport(params, d);

    expect(d.exportPdf).toHaveBeenCalledWith(
      TEMP_ARTIFACT,
      "D:\\Papers\\ATTENTION_VI.PDF",
      ORIGINAL,
    );
  });

  it("refuses when the translation artifact is unknown", async () => {
    const d = deps();

    const outcome = await runExport({ ...params, sourcePath: null }, d);

    expect(outcome).toEqual({
      status: "error",
      message: "There is no translated PDF to save yet.",
    });
    expect(d.showSaveDialog).not.toHaveBeenCalled();
  });

  it("reports the sidecar's message when the temp artifact is already swept", async () => {
    const d = deps({
      exportPdf: vi.fn(async () => {
        throw new Error(
          "404: The translated file is no longer available. Translate the document again, then save it.",
        );
      }),
    });

    const outcome = await runExport(params, d);

    expect(outcome.status).toBe("error");
    expect(outcome).toMatchObject({
      message: expect.stringContaining("no longer available"),
    });
  });

  it("reports a failing save dialog instead of throwing", async () => {
    const d = deps({
      showSaveDialog: vi.fn(async () => {
        throw new Error("dialog plugin unavailable");
      }),
    });

    const outcome = await runExport(params, d);

    expect(outcome).toEqual({
      status: "error",
      message: "dialog plugin unavailable",
    });
  });

  it("never proposes saving back into the temp dir the artifact came from", async () => {
    const d = deps();

    await runExport({ ...params, originalPath: null }, d);

    // Bare filename, so the dialog supplies its own folder rather than %TEMP%.
    expect(d.showSaveDialog).toHaveBeenCalledWith(
      expect.objectContaining({ defaultPath: "paper_translated_v003_vi.pdf" }),
    );
  });
});

describe("formatBytes", () => {
  it("formats the sizes shown in the save confirmation", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });

  it("does not blow up on a missing size", () => {
    expect(formatBytes(Number.NaN)).toBe("0 B");
    expect(formatBytes(-1)).toBe("0 B");
  });
});
