/**
 * Where pdf.js goes looking for the assets it does not carry in its bundle.
 *
 * pdf.js 5.x keeps several kinds of asset outside the worker bundle and fetches
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
 *   This one needs `useSystemFonts: false` alongside it or it does almost
 *   nothing: `fetchStandardFontData` returns null before it ever reads the
 *   prefix for every name but `Symbol` and `ZapfDingbats` while system fonts
 *   are allowed, which is the webview default.
 *
 * There is a fourth directory, `iccs/`, holding the CMYK ICC profile, and it is
 * deliberately not here. `iccUrl` is only ever read by `CmykICCBasedCS`'s
 * constructor, which `IccColorSpace.setOptions` makes unreachable the moment
 * `useWorkerFetch` is false — see the pin in `usePdfDocument.ts`. Setting it
 * would publish 500 KB that nothing fetches and claim a fix that does not
 * happen; DeviceCMYK keeps pdf.js's approximation, exactly as it did before
 * #77. Colour-managed CMYK needs the worker to do its own fetching, which is a
 * CSP question for its own issue rather than a line to add here.
 *
 * The trailing slash is load-bearing rather than tidy. pdf.js concatenates the
 * asset's filename straight onto these strings, and `getFactoryUrlProp` throws
 * `Invalid factory url` when one does not end in a slash. That throw lands on
 * every document load, so dropping a slash would take the entire viewer down
 * rather than only the pages the prefix exists to rescue — a much worse failure
 * than any of the ones being fixed. `vite.config.ts` imports the map below and
 * strips that slash to get its routes, so the two cannot drift apart.
 *
 * The prefixes are relative on purpose: the app is served from Vite's dev
 * server in development and from Tauri's custom protocol in a bundle, and
 * resolving against the caller's own base URI is what lets one value cover both
 * without either origin being written down anywhere.
 */

/** The directories, exactly as `pdfjs-dist` names them. `vite.config.ts`
 *  publishes these same names verbatim by importing the map rather than
 *  repeating it, because a pair that can drift is how #73 and #77 both read. */
const ASSET_DIRECTORIES = {
  wasm: "wasm/",
  cmaps: "cmaps/",
  standardFonts: "standard_fonts/",
} as const;

export type PdfAssetDirectory = keyof typeof ASSET_DIRECTORIES;

/** The directory names as they appear inside `pdfjs-dist` and, unchanged, in
 *  the published bundle. Exported for `vite.config.ts`, which publishes them,
 *  and for the test that holds them against the installed package. */
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
