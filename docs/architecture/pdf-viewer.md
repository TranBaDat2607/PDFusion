# PDF viewer

How both panes render, and the invariants that keep them fast and correct.

Both panes are `components/pdf-viewer/PdfViewer.tsx` (#30). Its pure half lives
in `lib/pdf-viewer/` and is unit-tested. The rest is imperative pdf.js work kept
out of React state. There are five invariants, and each one is easy to break by
"simplifying":

- **A slot's size comes from the layout model, never from a canvas.** Only the
  visible pages ±`RENDER_RADIUS` (3) hold a canvas (`page-renderer.ts`). Every
  other page is released: its render cancelled, its backing store zeroed
  (`canvas.width = 0`, not left to GC), its text layer removed, and
  `page.cleanup()` called a few releases later (see below). So the column's
  height, go-to, zoom anchoring and "which page am I
  on" are all computed from `layout.ts:pageGeometry`, and the JSX sizes each
  slot through the *same* `slotSize`. Round differently in one place and a
  go-to lands on the wrong page a few hundred pages in. Slots use `ring-1`
  rather than `border` for the same reason: a border would inset the canvas.
  Page sizes start as page 1's and are corrected in the background; Chromium's
  scroll anchoring keeps the reader in place meanwhile.

  **`page.cleanup()` is deferred, because it is not the cheap half of a
  release** (#76). The canvas goes immediately — it is the larger allocation,
  and one fast scroll drops dozens of them. `PDFPageProxy.cleanup()` is a
  different thing: it clears the proxy's `objs`, which holds the page's
  *decoded* images, so the next visit re-runs the decode. pdf.js's worker-side
  `GlobalImageCache` does not cover that, because `shouldCache` keys on the set
  of page indices an image ref was seen on and refuses anything under two — so
  art that appears on exactly one page, which is what a slide deck or a
  figure-heavy paper is made of, is never cached. Measured on the 78-page
  Beamer deck from #73/#74, whose figures are JPEG 2000: rebuilding one page's
  operator list after a `cleanup()` costs 200–410 ms on Windows (827 ms on
  Linux, #76) against 0.02 ms with the proxy left alone, and 5.7 s across the
  whole deck against 0.9 ms. End to end in the viewer, scrolling five pages
  away from a figure page and back took 384–753 ms to repaint before this and
  16–36 ms after. `lib/pdf-viewer/decode-retention.ts` holds the last
  `DECODE_RETAIN` (6) released pages back from cleanup, and a page scrolled
  back into the window moves to the *end* of that list rather than being
  cleaned up behind the reader. It moves rather than leaving, because the list
  is also the record of what still owes a `cleanup()`: a page taken off it and
  then never re-rendered — which is what a scroll sweeping straight past a page
  does, since the pump only reaches one page at a time — would hold its decoded
  images until the document was destroyed, and the count would stop bounding
  anything. For the same reason the deferred cleanup is handed the document it
  belongs to rather than reading `this.doc` back: it settles in a microtask, by
  which point a swap has already reassigned that field, so the cleanup would be
  aimed at the incoming document's pages. What the flush on the swap path does
  *not* do is free memory — `usePdfDocument` destroys the outgoing proxy in a
  cleanup React runs before the effect that calls `setDocument`, so by then
  the document is gone and the flush is a no-op. It earns its place by clearing
  the list, which must not survive into a document it does not describe. Note
  that a destroyed document's `getPage()` **throws synchronously** (its
  message handler is `null`) rather than rejecting, so every cleanup goes
  through `cleanupPage` in `lib/pdf-viewer/document-lifetime.ts`, which
  swallows it. Inside an effect, that throw unmounted the whole app.

  A decoded 2.2 MP image is ~9 MB and these pages carry two, so six retained
  pages is roughly 110 MB on that document — and the viewer mounts two panes,
  each with its own renderer and its own six, so the figure to budget against
  is ~220 MB. That is what retention *adds*: the `RENDER_RADIUS` pages inside
  the window hold their decodes too, as they always have, so the viewer's real
  ceiling on that deck is higher. An ordinary document costs nothing either
  way. The bound is a *count*, which is also why this is not simply a larger
  `RENDER_RADIUS`: that would multiply canvases too.

  The other half of #76 — decoding ahead of the canvas radius with
  `getOperatorList()` — was tried on paper and **rejected**. `getOperatorList()`
  passes `isOpList = true`, which adds `RenderingIntentFlag.OPLIST` to the
  rendering intent, and the intent is the first component of the cache key. It
  therefore allocates a different `intentState` from the one `render()` later
  uses, each pumping its own operator list; combined with the `shouldCache`
  rule above (both builds report the same page index), the worker would decode
  the page's images *twice*. A prefetch that shares state with the later render
  would have to be a real `render()` at the same intent into a throwaway
  canvas, which walks the whole operator list on the main thread and competes
  with scrolling. The queue already renders — and so decodes — the ±3 buffer
  pages ahead of the visible ones, which is the same thing for free.
- **A canvas is only ever replaced by a finished one.** Zoom resizes the slots,
  and the existing canvas stretches with them (`.pdf-page-canvas` is
  `width/height: 100%`, and `--scale-factor` rescales the text layer in the same
  frame). The re-render waits for `ZOOM_SETTLE_MS` of no further zooming, and
  each canvas is swapped in only when its render resolves. The reader's
  position is kept as page + fraction (`scrollAnchor` / `offsetForAnchor`) in a
  layout effect, before paint. Canvases are capped at `MAX_CANVAS_PIXELS`
  (4096²) and CSS-upscaled beyond that. Renders run one at a time from a
  priority list recomputed after every page, so a fast scroll never leaves a
  backlog of renders for pages it has passed.
- **The translated pane swaps pages, not documents.** With
  `incrementalUpdates`, each new rolling PDF loads off-screen while the old one
  stays up (`hooks/usePdfDocument.ts`), and then only the rendered pages that
  changed are repainted. Which pages changed comes from `translatedChanges`, an
  append-only log in the store (`adoptTranslatedArtifact(path, pages_in_chunk)`),
  **not** from "the latest `chunk_ready`". Two chunk events parsed from one
  network read are one React render, so a viewer that read only the latest
  would never repaint the other chunk's pages. Changes also accumulate across
  loads overtaken by the next chunk (`artifact-swap.ts`). Every replaced
  `PDFDocumentProxy` is `destroy()`ed; before this, every chunk leaked one into
  the worker. Only the *replaced* one: the load effect's cleanup runs on every
  new path and every reload while the old document is still on screen, so it
  may stop a load in flight but must spare a document that arrived
  (`trackDocumentLoad`). It once destroyed that one too; a Re-translate (which
  bumps the reload key) then left the pane drawing from a dead document, and
  the next page cleanup threw and blanked the window. Every async render
  re-checks a document *generation*, so a render that lost a race is dropped
  rather than drawn. A failed swap keeps the previous version on screen. None of this removes the need for the backend's
  `keep=2` (see "Rolling-PDF pruning keeps two files" in
  [translation-pipeline.md](translation-pipeline.md)): the old version is still
  what's displayed, and still what pdf.js issues range requests against, until
  the new one has loaded.
- **During a run the translated pane is blank except for translated pages.**
  Every rolling PDF carries the original wherever a chunk hasn't finished, so
  drawing what the file holds showed the source text first and swapped the
  translation over it later. `store.translationMask` records, *per rolling
  path*, which pages that file has translated
  (`lib/pdf-viewer/translation-mask.ts`). The pane draws only those, and
  `PageRenderer` checks the mask in `needsRender` and again after every
  `await`. A newly translated page sweeps in under white strips, one per band,
  each sliding off to the right shortly after the one above
  (`PageRenderer.streamIn`, timing in `streamBand`).
  - **Why strips, not `clip-path`:** the first version rewrote the canvas's
    `clip-path` every frame from `requestAnimationFrame`. That repaints the
    full-resolution bitmap per frame and shares the main thread with pdf.js,
    which during a cache hit is rendering the next page for the whole sweep,
    so it stuttered. The strips animate only `transform` (Web Animations),
    which the compositor runs without repainting. The bands also overlap,
    three in flight at once: back to back, each got about five frames and
    read as a stepped wipe.
  - **Why per path, not per run:** when chunk N lands, the pane is still on
    version N-1 until N loads. "Chunk N's page may draw" applied to N-1 would
    paint the original onto it, the very flash this exists to prevent. So
    `PdfViewer` asks for the mask of the path it actually *loaded*, and runs
    `setMask` after `setDocument` in the same commit.
  - **Every version is kept.** Each new path cancels the load in flight, so a
    pane can sit on an old version for many chunks, and a capped list once
    blanked pages already shown translated.
  - **The mask outlives the run.** Terminal events *finish* it
    (`finishMask(mask, translatedPdfPath)`); they don't clear it. `done` lands
    straight after the last `chunk_ready`, usually before that file has loaded,
    and lifting the mask then would draw the previous version's untranslated
    page. It lifts only once its result is the file on screen. Dismiss leaves
    it alone for the same reason; opening another document or abandoning the
    run clears it.
  - **Before the first chunk** there is no translated file, so `MainLayout`
    points the pane at the original, which has exactly the right shape, and the
    mask blanks every page of it. That path never reaches `translatedPdfPath`,
    so Save and Open can't pick it up.
  - **Only a translation sweeps in.** Pages outside a page selection appear
    without the sweep when the mask lifts. A cache hit's `chunk_ready` spans
    every page, so it adopts with the selection as its translated pages. Pages
    queued to sweep but not yet drawn are forgotten when a run starts or ends
    (`maskTransition`'s `forget`). Kept, they sweep in during the next run, or
    whenever the reader first scrolls to them long after this one.
  - **Sweep timing:** 8 bands over 640 ms is about five frames a band. At 14
    bands over 520 ms each band got two frames, and the left-to-right half of
    the motion never registered. `prefers-reduced-motion` skips the sweep.
- **Text layers are core `pdfjs.TextLayer`, plus two pieces copied from
  `pdf_viewer.mjs` rather than imported.** `pdf-viewer.css` carries its
  `.textLayer` rules, and `text-selection.ts` ports `TextLayerBuilder`'s
  drag-selection fix. `pdf_viewer.mjs` itself reads `globalThis.pdfjsLib` when
  it's evaluated, so importing it would depend silently on import order.
  Re-check both files against the new pdfjs-dist when bumping it. Find
  (`hooks/usePdfFind.ts`, `lib/pdf-viewer/find.ts`) reads text from the
  document, not from text layers (only rendered pages have one). It folds case
  and diacritics, `đ` → `d` included, and paints highlights onto rendered pages
  only. Highlight positions map to `textDivs` one-for-one, because `TextLayer`
  creates one span per item with a `str`, empty strings included.
- **Shortcuts go to one pane, and are always claimed.** `MainLayout` owns a
  capture-phase `keydown` handler (`lib/pdf-viewer/shortcuts.ts`). Ctrl+O opens a
  file. Ctrl+F, F3, Ctrl+± and Ctrl+0 go to the pane last clicked or focused,
  or to the other pane if that one is empty. Handled keys are
  `preventDefault`ed even when there's nothing to act on, because WebView2's
  own find bar only stands down for keys the page takes. Nothing fires while a
  dialog is open.
- **pdf.js fetches three directories of assets at runtime, and they have to be
  there.** pdf.js 5.x keeps them out of the worker bundle and loads each from
  its own prefix option on `getDocument`. Every one defaults to `null` and warns
  about nothing, which is how two issues shipped. `wasmUrl` (JPEG 2000, JBIG2)
  unset meant a viewer that asked for `nullopenjpeg.wasm` and dropped every page
  built on a JPX image or soft mask — twenty consecutive pages of a Beamer deck
  (#73). `cMapUrl` + `cMapPacked` (the 169 predefined CJK CMaps) unset means a
  Type0 font that names one instead of embedding its encoding fails to translate
  outright, so none of that text draws; `standardFontDataUrl` (the 14 standard
  fonts' metrics) unset means a non-embedded standard font is substituted from
  the system, so shapes and widths are wrong (#77). The first of those is not
  exotic here: the default target is Vietnamese and the tool exists to open
  documents in other scripts.

  Four things hold the fix together. Each prefix **must end in `/`**, or
  `getFactoryUrlProp` throws on every document load and no page renders at all.
  The files **must keep their own names**, because pdf.js concatenates a literal
  filename onto the prefix — which is why `vite.config.ts`'s
  `pdfusion:pdfjs-assets` plugin copies the directories verbatim instead of
  routing them through the `?url` import the worker uses, and why each ships
  whole rather than a chosen subset: which cmap or font a document wants is only
  knowable when it is opened. That plugin **imports the directory map from
  `lib/pdf-viewer/asset-urls.ts`** rather than repeating it: two lists that can
  disagree is how a rename ships a viewer which 404s at the first document load
  with every test still green. `useWorkerFetch` is **pinned to `false`** rather
  than left to pdf.js, which would otherwise derive it from whether the base URI
  is http (`isValidFetchUrl` tests `/https?:/`) — Tauri serves
  `http://tauri.localhost` on Windows but `tauri://localhost` on Linux and
  macOS, so the default would fetch from the worker on Windows and dev and from
  the main thread everywhere else, moving the CSP surface on one platform only.
  And the CSP needs `script-src 'self' 'wasm-unsafe-eval'`, without which
  `WebAssembly.instantiate` is refused. **That last half is invisible in
  `pnpm tauri dev`** — Tauri injects no CSP into a Vite-served page — so a change
  here is only really tested by `pnpm tauri build`.

  **A prefix on its own is not a fix — two of the three need a companion flag,
  and both were set only after a review caught the prefix doing nothing.**
  `cMapPacked: true` looks like a restatement of the default and is not: on the
  non-worker path `fetchBuiltInCMap` appends `.bcmap` only when it is set, and
  derives `isCompressed` from it, so leaving it out asks for an extensionless
  file and then reads the bytes as if they were not compressed.
  `standardFontDataUrl` is close to inert without **`useSystemFonts: false`**,
  because `fetchStandardFontData` returns `null` before it ever reads the prefix
  for every name but `Symbol` and `ZapfDingbats` while system fonts are allowed
  — and allowed is the webview default (`useSystemFonts` defaults to
  `!isNodeJS && !disableFontFace`). Turning it off is also the more faithful
  choice for a tool whose promise is layout preservation: the bundled Foxit and
  Liberation metrics are the real ones for the standard 14, where a system
  substitute's widths are a guess that differs per platform.

  That default is a trap for the tests as much as the app. `asset-urls.test.ts`
  runs in Node, where `useSystemFonts` defaults to `false`, so a fixture test
  that does not pass the app's own flags proves a configuration the app never
  runs. Its `assetOptions()` therefore mirrors `usePdfDocument.ts` **including
  the flags**, and one test pins the trap itself by asserting the silence you
  get with system fonts allowed.

  **`iccUrl` is deliberately not set, and `iccs/` is deliberately not
  published.** pdf.js does ship a CMYK ICC profile, and reaching for it looks
  like a free fourth line, but `IccColorSpace.setOptions` starts with
  `if (!useWorkerFetch) { this.#useWasm = false; return; }` — which makes
  `IccColorSpace.isUsable` false, so `CmykICCBasedCS.isUsable` never enters its
  body and the constructor that is the only reader of `iccUrl` is unreachable on
  every platform. Setting it publishes half a megabyte nothing fetches and
  claims a fix that does not happen; DeviceCMYK keeps pdf.js's approximation
  either way, and not even the `CMYK fallback: DeviceCMYK` warning fires, since
  that too lives inside the skipped branch. The same reasoning retires the "and
  the ICC transform" that used to be attached to `wasmUrl` here: `qcms_bg.wasm`
  is in `wasm/` and is equally unreachable under the pin. Colour-managed CMYK
  needs `useWorkerFetch: true`, which moves the CSP surface and can only be
  settled under `pnpm tauri build` on two platforms — its own issue, not a line
  to add here.

  The companion `lib/pdf-viewer/asset-urls.ts` keeps the trailing slash and the
  directory names under test against the installed pdfjs-dist, and two ~1 KB
  fixture PDFs in `lib/pdf-viewer/__fixtures__/` open through the real pdf.js on
  every test run — a predefined-CMap document and a non-embedded-Helvetica one,
  which is what #77 lacked. They are marked `binary` in `.gitattributes`: a
  PDF's trailer points at its xref by absolute byte offset, and they are ASCII
  enough that git's own heuristic misses them, so a CRLF checkout would break
  them on any Windows clone.
- **On Linux that decoder only *runs* because the shell asks for it.** pdf.js's
  `openjpeg.wasm` is built with WebAssembly Relaxed SIMD, and WebKitGTK carries
  the feature but defaults it off, so the module fails to parse. pdf.js reacts
  by quietly loading `openjpeg_nowasm_fallback.js` — pages still render, about
  three times slower, which reads as a heavy document rather than a defect. So
  `lib.rs:enable_wasm_relaxed_simd` sets `JSC_useWasmRelaxedSIMD=1` as the first
  statement of `run()`, before anything Tauri builds: `set_var` races any thread
  reading the environment, and WebKitGTK's web process picks the value up when
  it is forked (#74). It is set **only when absent**, so
  `JSC_useWasmRelaxedSIMD=0` still turns it off. No other platform needs this —
  Relaxed SIMD is on by default in Chrome 114+ and Safari 18.4+ — and the whole
  thing can be deleted once WebKitGTK enables it, at which point it is already a
  no-op. Measured on a 78-page deck of JPX slides: **2553 ms per page on the
  fallback, 827 ms with the WASM decoder, 7 ms for a vector-only slide.** The
  same decode costs 287 ms under Node's V8, so a ~2.9x gap to Chromium remains
  and is JavaScriptCore's, not something this app can reach.

- **Fonts are drawn as paths, never registered (`disableFontFace: true`).**
  By default pdf.js turns every font object into a `FontFace` and adds it to
  the document's font set, and each one costs the main thread a pass over the
  text layers already on screen. A translated paper is the worst case: one
  BabelDOC run per page means every page brings its own 10–20 font objects
  (`document.fonts` climbed 51 → 133 in nine pages). Measured in the app's
  translated pane on a cache hit, the main thread was blocked 430–910 ms of
  every second and page 5 arrived at 10.5 s; with the flag set, 160–235 ms and
  3.7 s. Subsetting the fonts (`pdf_pages.subset_embedded_fonts`) shrank the
  bytes but not the count, which is why it barely moved this. Reproduced
  outside the app with the app's text layer: 9 pages took 6.1 s → 3.9 s in
  WebKitGTK 2.52 and 4.4 s → 2.7 s in Chromium 149, and without a text layer
  the flag made no difference in either — so it is the text layer the
  registrations are expensive against, not one engine, and the flag is set on
  every platform. The options live in `lib/pdf-viewer/load-options.ts`, which
  the fixture tests share; pdf.js defaults the flag to `isNodeJS`, so a test
  that left it out would pass while the app ran without it.
