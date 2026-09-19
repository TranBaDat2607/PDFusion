/**
 * Where pdf.js goes looking for its WebAssembly image decoders.
 *
 * pdf.js 5.x stopped carrying the JPEG 2000, JBIG2 and ICC decoders inside its
 * worker bundle; it fetches them at runtime from the `wasmUrl` prefix handed to
 * `getDocument`. That option defaults to `null`, and nothing warns when it is
 * left that way — which is how #73 shipped. The worker asked for the literal
 * path `nullopenjpeg.wasm`, took the 404, fell back to importing
 * `nullopenjpeg_nowasm_fallback.js`, took that 404 too, and handed back a null
 * decoder. Every page carrying a JPEG 2000 image or soft mask then failed to
 * render, which on a Beamer deck whose figures were JPX meant twenty
 * consecutive pages of nothing.
 *
 * The trailing slash is load-bearing rather than tidy. pdf.js concatenates the
 * decoder's filename straight onto this string, and `getFactoryUrlProp` throws
 * `Invalid factory url` when it does not end in one. That throw lands on every
 * document load, so dropping the slash would take the entire viewer down rather
 * than only the JPX pages this exists to rescue — a much worse failure than the
 * one being fixed.
 *
 * The prefix is relative on purpose: the app is served from Vite's dev server
 * in development and from Tauri's custom protocol in a bundle, and resolving
 * against the caller's own base URI is what lets one value cover both without
 * either origin being written down anywhere.
 */

/** Must match the directory `vite.config.ts` publishes the decoders into.
 *  The two are a pair — moving one without the other restores #73. */
const WASM_DIRECTORY = "wasm/";

/**
 * The absolute prefix to pass as `getDocument`'s `wasmUrl`.
 *
 * Takes the base URI rather than reading `document.baseURI` itself, so the
 * trailing-slash invariant above can be covered by a test with no DOM — the
 * same reason the rest of `lib/pdf-viewer/` takes its inputs as arguments.
 */
export function pdfWasmUrl(baseUrl: string): string {
  return new URL(WASM_DIRECTORY, baseUrl).href;
}
