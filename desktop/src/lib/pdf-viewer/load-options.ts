/**
 * The options every document is opened with, apart from where its bytes come
 * from. One function, so the fixture tests in `asset-urls.test.ts` load their
 * PDFs exactly the way the viewer does: they used to repeat these flags by
 * hand, and a flag missing there is a configuration that never runs in the app
 * being proven instead.
 *
 * `prefix` maps an asset directory to the URL pdf.js fetches it from: the app
 * resolves it against `document.baseURI` (`pdfAssetUrl`), the tests point it at
 * the installed package.
 */

import type { PdfAssetDirectory } from "./asset-urls";

export function pdfDocumentOptions(prefix: (dir: PdfAssetDirectory) => string) {
  return {
    // pdf.js fetches all three of these at runtime and defaults every one of
    // them to null, warning about none: an unset prefix costs the JPX pages
    // (#73), then the CJK text and the standard-font metrics (#77). See
    // asset-urls.ts.
    wasmUrl: prefix("wasm"),
    cMapUrl: prefix("cmaps"),
    // Load-bearing on this path, not a restatement of the default:
    // `fetchBuiltInCMap` appends `.bcmap` and sets `isCompressed` from it, so
    // an unset value asks for an extensionless file and then reads the bytes
    // as if they were not compressed.
    cMapPacked: true,
    standardFontDataUrl: prefix("standardFonts"),
    // Otherwise pdf.js substitutes a non-embedded standard font from whatever
    // the webview has, and the prefix above is consulted for Symbol and
    // ZapfDingbats alone. The substitute's widths are a guess that differs per
    // platform; the bundled Foxit metrics are the real ones, which is the
    // whole point of shipping them.
    useSystemFonts: false,
    // Pinned, because pdf.js would otherwise derive it from whether the base
    // URI is http: Tauri serves http://tauri.localhost on Windows but
    // tauri://localhost on Linux and macOS, so the default would fetch from
    // the worker on Windows and from the main thread everywhere else. One path
    // keeps the CSP surface where #73 left it.
    useWorkerFetch: false,
    // Draw glyphs as paths rather than registering each font with the
    // webview. Every registered FontFace costs the main thread a pass over the
    // text layers on screen, and a translated paper brings 10–20 font objects
    // per page (one BabelDOC run per page, each with its own copies): in the
    // app's translated pane that kept the main thread 40–90% busy and took page
    // 5 from 3.7 s to 10.5 s. The same penalty shows in Chromium, so it is not
    // a WebKitGTK quirk to gate by platform. The cost moves into painting,
    // which stayed at a few hundred milliseconds a page.
    disableFontFace: true,
  };
}
