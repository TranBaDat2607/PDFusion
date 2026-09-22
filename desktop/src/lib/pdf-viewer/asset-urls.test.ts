import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

import {
  pdfAssetDirectories,
  pdfAssetUrl,
  type PdfAssetDirectory,
} from "./asset-urls";

const directories = Object.keys(pdfAssetDirectories) as PdfAssetDirectory[];

describe("pdfAssetUrl", () => {
  // The one property pdf.js will throw over. `getFactoryUrlProp` rejects a
  // prefix without a trailing slash on every document load, so this assertion
  // stands between the asset fixes and a viewer that opens nothing at all.
  it("ends in a slash whatever the base looks like", () => {
    for (const base of [
      "http://localhost:1420/",
      "http://localhost:1420/index.html",
      "tauri://localhost/",
      "http://tauri.localhost/index.html",
    ]) {
      for (const dir of directories) {
        expect(pdfAssetUrl(dir, base).endsWith("/")).toBe(true);
      }
    }
  });

  // Dev and bundle reach the assets over different origins; one relative
  // prefix has to cover both, so neither origin appears in the source.
  it("resolves against the dev server", () => {
    expect(pdfAssetUrl("cmaps", "http://localhost:1420/index.html")).toBe(
      "http://localhost:1420/cmaps/",
    );
  });

  it("resolves against the bundled app's custom protocol", () => {
    expect(pdfAssetUrl("wasm", "tauri://localhost/")).toBe(
      "tauri://localhost/wasm/",
    );
  });

  // Vite emits the assets beside index.html, not at the server root, so a
  // base that carries a path has to keep it — an absolute "/cmaps/" would 404
  // wherever the app is not served from the root.
  it("stays beside index.html when the app sits under a subpath", () => {
    expect(
      pdfAssetUrl("standardFonts", "http://example.test/app/index.html"),
    ).toBe("http://example.test/app/standard_fonts/");
  });

  it("returns an absolute url, since pdf.js validates it as a fetch target", () => {
    for (const dir of directories) {
      expect(() => new URL(pdfAssetUrl(dir, "tauri://localhost/"))).not.toThrow();
    }
  });

  // The names here and the ones vite.config.ts copies are the same names, and
  // both are really pdfjs-dist's. A bump that relocates one should fail here
  // rather than 404 at the first document load.
  it("names directories that pdfjs-dist actually ships", () => {
    for (const dir of directories) {
      const source = path.join(pdfjsRoot, pdfAssetDirectories[dir]);
      expect(fs.existsSync(source), `${source} is missing`).toBe(true);
      expect(fs.readdirSync(source).length).toBeGreaterThan(0);
    }
  });
});

const require = createRequire(import.meta.url);
const pdfjsRoot = path.dirname(require.resolve("pdfjs-dist/package.json"));
const fixtures = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "__fixtures__",
);

/** The same prefixes the app hands `getDocument`, pointed at the package
 *  instead of a server — in Node pdf.js reads them off the filesystem. */
function assetOptions() {
  // A forward slash even on Windows: pdf.js concatenates filenames onto this
  // and rejects a prefix that does not end in one, whatever the platform.
  const prefix = (dir: PdfAssetDirectory) =>
    `${path.join(pdfjsRoot, pdfAssetDirectories[dir])}/`;
  return {
    wasmUrl: prefix("wasm"),
    cMapUrl: prefix("cmaps"),
    cMapPacked: true,
    standardFontDataUrl: prefix("standardFonts"),
    iccUrl: prefix("iccs"),
    useWorkerFetch: false,
  };
}

async function textOf(fixture: string, options: Record<string, unknown>) {
  // The legacy build, because this runs in Node: it substitutes a filesystem
  // reader for the DOM one, which is what lets the prefixes above be paths.
  const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
  const warnings: string[] = [];
  const warn = vi
    .spyOn(console, "warn")
    .mockImplementation((...args: unknown[]) => {
      warnings.push(args.join(" "));
    });
  try {
    const doc = await pdfjs.getDocument({
      data: new Uint8Array(fs.readFileSync(path.join(fixtures, fixture))),
      ...options,
    }).promise;
    const page = await doc.getPage(1);
    // The operator list is what pulls the fonts in; text content alone does
    // not, so without it a missing cmap would go unnoticed here.
    await page.getOperatorList();
    const content = await page.getTextContent();
    const text = content.items
      .map((item) => ("str" in item ? item.str : ""))
      .join("");
    await doc.destroy();
    return { text, warnings };
  } finally {
    warn.mockRestore();
  }
}

// #77 shipped because nothing opened a document of either kind. These two are
// ~1 KB each and take about a second, so now something does on every run.
describe("the assets pdf.js fetches at runtime", () => {
  it("renders a predefined CJK CMap only when cMapUrl is set", async () => {
    const withPrefixes = await textOf("cjk-cmap.pdf", assetOptions());
    expect(withPrefixes.text).toBe("あい");
    expect(withPrefixes.warnings).toEqual([]);

    // What the viewer did before this fix: the font fails to translate and the
    // page carries no text at all.
    const { wasmUrl, useWorkerFetch } = assetOptions();
    const asShipped = await textOf("cjk-cmap.pdf", { wasmUrl, useWorkerFetch });
    expect(asShipped.text).toBe("");
    expect(asShipped.warnings.join(" ")).toContain("cMapUrl");
  });

  it("loads a non-embedded standard font only when standardFontDataUrl is set", async () => {
    const withPrefixes = await textOf("standard-font.pdf", assetOptions());
    expect(withPrefixes.text).toBe("Standard font Helvetica");
    expect(withPrefixes.warnings).toEqual([]);

    // This one still draws text, from a substituted system font — the glyph
    // shapes and widths are wrong, which is why only the warning shows it.
    const { wasmUrl, useWorkerFetch } = assetOptions();
    const asShipped = await textOf("standard-font.pdf", {
      wasmUrl,
      useWorkerFetch,
    });
    expect(asShipped.warnings.join(" ")).toContain("standardFontDataUrl");
  });
});
