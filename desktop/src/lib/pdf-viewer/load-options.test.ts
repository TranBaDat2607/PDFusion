import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { beforeAll, describe, expect, it } from "vitest";

import { pdfAssetDirectories } from "./asset-urls";
import { pdfDocumentOptions } from "./load-options";

const require = createRequire(import.meta.url);
const pdfjsRoot = path.dirname(require.resolve("pdfjs-dist/package.json"));
const fixtures = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "__fixtures__",
);
const options = () =>
  pdfDocumentOptions(
    (dir) => `${path.join(pdfjsRoot, pdfAssetDirectories[dir])}/`,
  );

// See asset-urls.test.ts for why this is loaded once, up front.
let pdfjs: typeof import("pdfjs-dist/legacy/build/pdf.mjs");

beforeAll(async () => {
  pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
}, 60_000);

/** Whether each font page 1 loads would be registered with the document's
 *  font set (`false`) or drawn as paths by pdf.js (`true`). */
async function fontFaceFlags(overrides: Record<string, unknown> = {}) {
  const doc = await pdfjs.getDocument({
    data: new Uint8Array(
      fs.readFileSync(path.join(fixtures, "standard-font.pdf")),
    ),
    ...options(),
    ...overrides,
  }).promise;
  try {
    const page = await doc.getPage(1);
    // The operator list is what makes the worker hand the fonts over.
    await page.getOperatorList();
    const objs = (page as unknown as { commonObjs: Iterable<[string, unknown]> })
      .commonObjs;
    return [...objs]
      .map(([, data]) => data as { disableFontFace?: boolean; loadedName?: string })
      .filter((data) => data?.loadedName !== undefined)
      .map((font) => font.disableFontFace === true);
  } finally {
    await doc.destroy();
  }
}

describe("pdfDocumentOptions", () => {
  // Registering a font is what made the translated pane slow: every font
  // object pdf.js turns into a FontFace costs the main thread a pass over the
  // text layers already on screen, and a translated paper brings 10–20 per
  // page. With the text layer in place, measured on a 9-page translation,
  // drawing the glyphs as paths took the whole document from 6.1 s to 3.9 s in
  // WebKitGTK and from 4.4 s to 2.7 s in Chromium, so it is set everywhere.
  //
  // It has to be explicit: pdf.js defaults it to `isNodeJS`, which is `true`
  // here and `false` in the webview, so leaving it out would pass every test in
  // this file and change nothing in the app.
  it("has pdf.js draw fonts itself instead of registering them", () => {
    expect(options().disableFontFace).toBe(true);
  });

  it("reaches the fonts pdf.js loads", async () => {
    const drawn = await fontFaceFlags();
    expect(drawn.length).toBeGreaterThan(0);
    expect(drawn.every(Boolean)).toBe(true);
  });

  // Guards the probe above: it can tell the two modes apart.
  it("would register them if asked to", async () => {
    const drawn = await fontFaceFlags({ disableFontFace: false });
    expect(drawn.length).toBeGreaterThan(0);
    expect(drawn.some(Boolean)).toBe(false);
  });
});
