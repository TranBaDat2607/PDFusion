/**
 * Where pdf.js goes looking for the assets it does not carry in its bundle.
 *
 * pdf.js 5.x keeps four kinds of asset outside the worker bundle and fetches
 * each one at runtime from its own prefix option on `getDocument`. Every one of
 * those options defaults to `null`, and nothing warns when one is left that way
 * — which is how #73 and then #77 shipped:
 *
 * - `wasm/` — the JPEG 2000, JBIG2 and ICC transform decoders. Unset, the
 *   worker asked for the literal path `nullopenjpeg.wasm`, took the 404, fell
 *   back to importing `nullopenjpeg_nowasm_fallback.js`, took that 404 too, and
 *   handed back a null decoder. Every page carrying a JPEG 2000 image or soft
 *   mask then failed to render, which on a Beamer deck whose figures were JPX
 *   meant twenty consecutive pages of nothing (#73).
 * - `cmaps/` — the predefined CJK CMaps (`UniJIS-UCS2-H`, `GBK-EUC-H`, …) that
 *   a Type0 font may name instead of embedding its encoding. Unset, the font
 *   fails to load outright: `translateFont failed` and not one glyph of that
 *   text on the page. The default translation target is Vietnamese and the
 *   tool exists to open documents in other scripts, so this one is squarely in
 *   scope rather than exotic (#77).
 * - `standard_fonts/` — the metrics for the 14 standard fonts, which a PDF may
 *   reference without embedding. Unset, the text still draws, but from a
 *   substituted system font, so the glyph shapes and widths are wrong (#77).
 * - `iccs/` — the CMYK ICC profile. Unset, DeviceCMYK falls back to pdf.js's
 *   approximation rather than a colour-managed conversion (#77).
 *
 * The trailing slash is load-bearing rather than tidy. pdf.js concatenates the
 * asset's filename straight onto these strings, and `getFactoryUrlProp` throws
 * `Invalid factory url` when one does not end in a slash. That throw lands on
 * every document load, so dropping a slash would take the entire viewer down
 * rather than only the pages the prefix exists to rescue — a much worse failure
 * than any of the ones being fixed.
 *
 * The prefixes are relative on purpose: the app is served from Vite's dev
 * server in development and from Tauri's custom protocol in a bundle, and
 * resolving against the caller's own base URI is what lets one value cover both
 * without either origin being written down anywhere.
 */

/** Must match the directories `vite.config.ts` publishes verbatim.
 *  The two are a pair — moving one without the other restores #73 or #77. */
const ASSET_DIRECTORIES = {
  wasm: "wasm/",
  cmaps: "cmaps/",
  standardFonts: "standard_fonts/",
  iccs: "iccs/",
} as const;

export type PdfAssetDirectory = keyof typeof ASSET_DIRECTORIES;

/** The directory names as they appear inside `pdfjs-dist` and, unchanged, in
 *  the published bundle. Exported for the tests that hold those two together. */
export const pdfAssetDirectories = ASSET_DIRECTORIES;

/**
 * The absolute prefix to pass as one of `getDocument`'s asset URL options.
 *
 * Takes the base URI rather than reading `document.baseURI` itself, so the
 * trailing-slash invariant above can be covered by a test with no DOM — the
 * same reason the rest of `lib/pdf-viewer/` takes its inputs as arguments.
 */
export function pdfAssetUrl(dir: PdfAssetDirectory, baseUrl: string): string {
  return new URL(ASSET_DIRECTORIES[dir], baseUrl).href;
}
