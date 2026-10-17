# The translation pipeline

What a translation job produces, how BabelDOC is driven and kept fast, and the
two caches in front of it.

## Translation output lifecycle — nothing the pipeline writes is permanent

Everything a translation job produces is disposable:

- the rolling `{stem}_translated_v{N}.pdf` in `%TEMP%\pdfusion-translate-<rand>\`
  is wiped by the next job, by Tauri's `ExitRequested` handler, and by the
  sidecar's orphan sweep;
- the whole-PDF cache entry under `translated_pdf_cache/files/<sha>.pdf` is
  SHA-named, LRU-evicted at `pdf_cache_max_size_mb`, and cleared wholesale by
  `DELETE /config/cache`.

The user's only durable copy comes from **`POST /pdf/export`** (`utils/file_export.py`),
which copies the artifact to a destination picked in the native Save dialog via
a sibling staging file + `os.replace`, so a failed copy never leaves a truncated
PDF where the user believes a good one is.

Consequence for UI copy: **never label an unexported path "Saved to"**. The
frontend keeps the two apart in the Zustand store — `translatedPdfPath`
(ephemeral, drives the viewer) vs `exportedPdfPath` (permanent, the only one
allowed to say "Saved to"). Save / Open / Show-in-folder live in
`components/translation/TranslatedFileActions.tsx`, backed by
`hooks/useExportTranslated.ts` and the pure flow in `lib/export-pdf.ts`.
Open and reveal go through the app-defined Tauri commands
`open_path_in_default_app` / `reveal_path_in_file_manager` (`lib.rs`). These
are app commands rather than the opener plugin's JS API, whose `open-path`
capability scope would have to enumerate every folder a user might save into —
so `checked_pdf_path` substitutes a **file-type restriction** for that scope.
Keep it: `open_path` bottoms out in `ShellExecute` and the command is reachable
from the webview. The CSP narrows what can get *into* the webview; it does not
vet what a command is handed once something is there.

Three non-obvious invariants in this area, each with a test:

1. **`exportedPdfPath` is cleared when a new artifact arrives, never at job
   start.** Starting a job immediately `rmtree`s the previous job's temp dir
   (`processor.py:_schedule_temp_cleanup`), so clearing at start would discard
   the pointer to the user's saved copy — the only file still on disk — if the
   new run failed before producing anything. See `adoptArtifact` in
   `useTranslation.ts`.
2. **`cancelling` is a real status, not "cancelled early".** `cancel()` can't
   wait for the backend (a chunk mid-flight can't be hard-killed), and its
   drain runs `cleanup_partial_artifacts()`, which at its default `keep=1`
   unlinks all but the newest rolling PDF (mid-run the pipeline passes
   `keep=2` — see "Loading the layout model once"). So `TranslationState.status` has a `cancelling` state covering
   the window until the terminal SSE event. Anything that touches the artifact
   must gate on `isTranslationBusy()`, never on `status === "running"`.

   That window is the in-flight chunks' drain and nothing more, **because a
   chunk worker checks `task.cancelling()` before each page**
   (`_process_with_babeldoc.worker`). BabelDOC's `async_translate` catches the
   `CancelledError` a cancel delivers, waits for its thread and returns as if
   finished, so `run_one_chunk` sees only a chunk with no output. Without the
   check the worker went on to the next page, the `gather` in the pipeline's
   `finally` waited for all of them, and Cancel on a 50-page run sat at
   "Cancelling…" for minutes. The same `finally` runs on every error, so the
   check matters there too. This one has no unit test — the workers live in
   `processor.py`, which the suite doesn't import — so exercise Cancel on a
   long run in `pnpm tauri dev` after touching that loop.
3. **`export_pdf(protect=...)` refuses to overwrite the opened document.** The
   Save dialog lets the user type their source document's own name and confirm
   "Replace?", which would destroy their input with no undo. Relatedly,
   `suggestedExportName` *always* appends `_<lang>` — `paper_vi.pdf` →
   `paper_vi_vi.pdf` — so the suggestion can never collide with the source.
   Skipping the suffix when the stem "looks translated" guesses intent from a
   filename (`chapter_vi.pdf` is a Roman numeral) and manufactures exactly that
   collision.

## Loading the layout model once, and what that does *not* fix

BabelDOC reloads the DocLayout-YOLO ONNX model on every `TranslationConfig`
whose `doc_layout_model` is `None` (`format/pdf/translation_config.py:290-293`)
— it has no cache of its own. The pipeline builds one config per chunk, so a
50-page LLM run paid ~3-4 s × 50 for a model that never changes, *on the event
loop*, freezing every SSE stream and `/health` with it.
`processors/doc_layout_cache.py` holds it once per process behind
double-checked locking, and every fetch goes through `asyncio.to_thread`.

**Sharing one instance across concurrent sub-jobs is safe, and that is a
property of `OnnxModel`, not a general one.** `handle_document` takes
`self.lock` only around the PyMuPDF rasterization; `predict()` reads
`self._names`/`self._stride` and calls `InferenceSession.run`, which onnxruntime
supports concurrently on one session. Nothing else in babeldoc touches the
object — `il_creater`, `layout_parser` and `add_debug_information` only ever
call `handle_document`, and `init_font_mapper` (the one thing babeldoc's own
CLI attaches per-config, `main.py:730`) exists on `RpcDocLayoutModel`, not on
`OnnxModel`. Swap the model class and re-check all four before assuming this
still holds. One consequence: that lock is now process-wide, so page
rasterization serializes across in-flight chunks where it used to be per-chunk.
Negligible against ~3-4 s, but it is a real new serialization point.

A failed load leaves the module global `None` rather than caching the failure,
so the next chunk retries; `run_one_chunk`'s `except BaseException` still routes
it into `babeldoc_chunk_error`.

**What this does not fix, and why chunk size is a live question.** The layout model was never the largest per-chunk fixed cost.
`FontMapper.__init__` sha3_256-verifies and loads **all 34 embedding fonts
(~250 MB)** for `lang_out`, and nothing memoizes it — not `fontmap.py`, not
`assets.py`. BabelDOC constructs one per stage: `ILCreater`, `ParagraphFinder`,
`StylesAndFormulas`, `ILTranslator`, `Typesetting`, `PDFCreater`. Measured
against the installed babeldoc on a warm cache: **1.02 s per `FontMapper`, ≈6 s
per chunk**, none of which this cache touches. So "the reload is fixed" is not
on its own a reason to shrink a chunk — shrinking one multiplies ~6 s by the
extra chunks it creates, and on the Argos path (`_MAX_PARALLEL_CHUNKS_ARGOS = 2`)
that can outweigh what the cache saves. `_PAGES_PER_CHUNK_ARGOS` is 1 today,
like the LLM path; measure an end-to-end Argos run before trusting that, or
before changing either constant. What a larger chunk buys is amortized
per-chunk fixed overhead in general, not the ONNX reload specifically.

**Rolling-PDF pruning keeps two files, not one.** `cleanup_partial_artifacts`
runs after every chunk now, not only on cancel, so a long run no longer piles
up all N versions. Its `keep` defaults to 1 (the cancel path's "leave the
partial result as the sole survivor"), but the per-chunk call passes **2**. The
order per chunk is rebuild `v{N}` → prune → emit `chunk_ready(v{N})`, so at the
unlink the webview is still showing `v{N-1}` and pdf.js may still be issuing
range requests against it — `/pdf/file` advertises `Accept-Ranges` and
`FileResponse` opens and closes the file per request, so it is deletable
between ranges and the viewer renders the 404 as an error. Keeping one
superseded version still bounds disk at two files. (On Windows the unlink
usually loses that race and just logs, which is what makes the symptom
intermittent rather than absent — do not read "it works here" as "the ordering
is safe".)

## Page ranges and limits

A translation covers the pages the toolbar's Pages box names (#33) — `1-20, 35`,
blank for all of them — and `translation.max_pages` limits **those pages**, not
the document's length. A 500-page book is translated 50 pages at a time; before
this, anything past 50 pages opened the progress overlay and then failed with
"Too many pages: 120 > 50". `max_file_size_mb` still applies to the file,
whichever pages are chosen. Both limits are in Settings → Cache → Performance,
bounded by `config/models.py:MaxPages` / `MaxFileSizeMB`, which `PUT /config`
shares so an out-of-range value is a 422 rather than a 500.

- **One check, made twice.** `page_selection.validate_selection` is what
  `POST /translate` runs before creating the job (off the event loop, since it
  opens the PDF — and `page_selection.py` is stdlib-only, while
  `pdf_pages.py` imports `fitz` inside each function, because the pre-flight
  is on the request path) and what `PDFProcessor._validate_file` runs again inside it.
  The frontend has its own copy of the page half (`lib/page-range.ts:
  checkPageLimit`) only to turn a request over the limit into an offer —
  `PageLimitDialog`'s "Translate pages 1–50" — instead of a toast. When the page
  count isn't known yet, it skips that and the sidecar's 422 applies.
- **The output is always the whole document.** Only the selected pages are
  split into chunks, but `rolling_segments` fills every other page from the
  original, so the viewer's page count never changes and pages outside the
  selection stay readable. Neighbouring original pages are inserted as one run.
  The rebuild is cheap next to BabelDOC's ~6 s per page: measured at 0.4 s per
  rebuild for a 300-page, 77 MB PDF (0.23 s with a 10-page selection). It is
  per chunk, though, and it writes a full copy each time — which is why the
  size limit still exists.
- **Scheduling is by page, not chunk index.** `pick_next` measures a pending
  chunk's distance from the reader's page by its first page; with a selection,
  chunk 0 can be page 40.
- **A partial run is cached under its own key.** `_make_cache_key` appends
  `|pages=<pages_key>` only when a selection is given, so every whole-document
  key is what it was before, and a partial translation can never be served as
  the whole document's. A lookup with a selection tries the whole document's
  entry first — it has those pages translated too — and counts once.
- **Progress counts toward the selection.** `chunk_ready.pages_to_translate` is
  the denominator of "N of M pages translated"; `total_pages` stays the
  document's.
- **A selection of every page is the whole document**, on both sides
  (`selected_pages` and `parsePageRanges` return `None`/`null`), so it is
  cached and reported as a full translation.
- **The token estimate is rough on purpose.** Shown only when an LLM will run,
  it counts PyMuPDF's text blocks (not BabelDOC's paragraphs) and characters
  (not a provider's tokens), and each constant in `usage_estimate.py` errs
  high. That module is stdlib-only, and `test_usage_estimate.py` holds its
  per-paragraph prompt size to the prompts the translators really send. The
  Pages box is estimated again only after it has been still for
  400 ms, so typing `1-20` doesn't ask about `1-` — every page — on the way.

The Pages box resets when a different document opens (`setOriginalPdfPath`).
Accepting the offer writes the chosen pages back into it, so Re-translate and
Retry ask for the same pages.

## Each chunk's fonts are subset before it is merged

BabelDOC subsets fonts only in its clean step, and PDFusion passes
`skip_clean=True` (which `enhance_compatibility` would force anyway:
`skip_clean = skip_clean or enhance_compatibility` in BabelDOC 0.5.24). With
one page per BabelDOC job, every page therefore arrived with whole fonts —
~500 KB per Noto Serif style for a Latin target, ~14 MB for CJK — and the
rolling PDF carried a full copy per page: a 9-page paper came out at 6.8 MB
with 10.3 MB of embedded fonts across 123 font objects. pdf.js converts each
font object separately, so every page re-paid the parse. Removing duplicate
objects (`garbage=4`) shrinks the file but not that work: each page's font
dictionary differs, so pdf.js still sees 123 fonts.

`pdf_pages.subset_embedded_fonts` cuts each chunk's fonts to the glyphs it
uses, once per chunk (45–70 ms a page), rather than on the rolling PDF, which
is rebuilt after every chunk. Measured in the viewer on that paper, pages 1–5:
pdf.js worker time 2825 → 1496 ms, the fifth page done 34% sooner. It is
in-process MuPDF, not BabelDOC's 60-second subprocess: the native subsetter
took ≤106 ms even on 15 MB CJK fonts, and the process already runs far more
MuPDF in-process. A failure keeps the unsubset file — still a correct
translation, only a heavier one.

MuPDF's subsetter keeps glyph ids (these are Identity-H fonts addressed by id),
so `loca`/`hmtx` stay full length and a subset CJK font is still ~400 KB of
index; the outlines are what shrink (3.0 MB → 1.3 KB in the test fixture).

## Two-tier translation caching

Two independent, persistent SQLite caches sit on the translation path. Both live
under the app's data root (`utils/paths.appdata_dir`), open through
`storage/sqlite.py` (WAL + per-thread connections), are process-wide singletons,
and are content-addressed by SHA-256 — so neither is invalidated by re-runs with
identical inputs.

**Both schemas are versioned** (`storage/migrations.py`, #59). Each database
records its version in `PRAGMA user_version`, and a store brings its file up to
date on the first use of its connection — never in its constructor, because
`get_pdf_cache()` is evaluated on the event loop before `asyncio.to_thread` runs
the method, and a migration may rebuild a large table. Every step runs in one
`BEGIN IMMEDIATE` transaction with its version bump. To change a schema, append
a `Migration` to the store's `_MIGRATIONS`; never edit one that has shipped, and
never `ALTER TABLE` outside one — a swallowed `ALTER` is how `index.db` came to
exist in two shapes. Timestamps are INTEGER Unix milliseconds
(`storage/sqlite.now_ms`); v1 converted the local-time ISO strings the
unversioned code wrote, and `CacheHit.cached_at` goes out as ISO-8601 with a UTC
offset. A cache file written by a newer build is moved aside and started fresh
(`on_too_new="reset"`).

1. **Whole-PDF cache** (`processors/pdf_cache.py`, `get_pdf_cache()`). Keyed on
   `sha256(file_bytes) | lang_in | lang_out | service | model | PIPELINE_VERSION`.
   Source language *was* excluded, on the premise that it never changes output;
   that only held while the API pinned every request to `auto`. The LLM system
   prompts name the source explicitly, so `auto` and `en` must not collide —
   see the comment at `_make_cache_key`. A hit lets `process_pdf` **skip the entire BabelDOC
   pipeline**, copy the cached PDF into the live output dir, and emit synthetic
   SSE `progress`/`done` events. LRU-evicted to `pdf_cache_max_size_mb` (default
   1000 MB). Bump `PIPELINE_VERSION` when any BabelDOC config field that changes
   output (font, watermark mode, etc.) changes, or how that output is
   post-processed before it is cached (v2: fonts subset per chunk).
2. **Paragraph cache** (`translators/translation_cache.py`,
   `get_translation_cache()`). Keyed on `lang_in|lang_out|service|model|text`,
   with a TTL (`expires_at`). Memoizes individual `translate()` calls across all
   backends, so a partial/cancelled job still warms the cache for the next run.

Gating: the PDF cache is checked only when `settings.translation.cache_translated_pdfs`
is on and the request didn't pass `bypass_cache=true`; the paragraph cache is
gated by `settings.translation.cache_translations`. The "Re-translate" button in
the UI sends `bypass_cache=true` (`hooks/useTranslation.ts`).

What may be **written** to the PDF cache is a separate question, answered by
`pdf_cache.py:is_cacheable_artifact`: the file must be a real
`{stem}_translated_v*.pdf` in the job's own output dir, **and the run must have
had zero failed paragraphs**. A cache entry is keyed by the input's hash, so a
partly-untranslated artifact would be served on every later open of that file
and stays invisible until someone clicks Re-translate.
