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

**What this does not fix, and why chunk size is a live question.** The layout
model was never the largest per-chunk fixed cost: the fonts were.
`FontMapper.__init__` sha3_256-verifies and loads **all 34 embedding fonts
(266 MB)** for `lang_out`, and BabelDOC memoizes neither — not `fontmap.py`, not
`assets.py`. It constructs one per stage: `ILCreater`, `ParagraphFinder`,
`StylesAndFormulas`, `ILTranslator`, `Typesetting`, `PDFCreater` (a second for
scanned-page detection) — 6 per page on the Argos path, 7 on the LLM path,
counted on real runs. The hash is now remembered and the fonts loaded once per
process (the next two sections), but every chunk is still a whole BabelDOC run,
with its own IR, rendering and PDF save. `_PAGES_PER_CHUNK_ARGOS` is 1 today,
like the LLM path; measure an end-to-end Argos run before changing either
constant. What a larger chunk buys is amortized per-chunk fixed overhead in
general, not the ONNX reload specifically.

## A font that passed its hash check is not hashed again

A `FontMapper` took ~1.3 s, of which ~1.0 s was `assets.verify_file` hashing
the 34 fonts (measured alone: 0.99 s; parsing them alone: 0.13 s; the ~0.26 s a
mapper still cost with the hash remembered was that parse plus the rest of the
constructor, under the contention of a real run — a parse only a process's first
mapper pays now, as the next section explains). That is **238 checks per page
— 34 fonts × 7 mappers (204 on Argos, with 6) — against the same 34 (file,
expected hash) pairs**, none derived from the document; traced over real
one-page runs on two different documents, every pair passed, and every file was
byte-identical before and after both runs. Only a process's
first check can find anything (a missing or corrupt font, which BabelDOC then
re-downloads); the rest re-hash unchanged bytes.

`processors/font_verify_cache.py` replaces the module global
`babeldoc.assets.assets.verify_file` with a memo — BabelDOC's asset functions
call it by that global name, so that one attribute reaches every stage; re-check
it when bumping babeldoc. Measured on a one-page run: **15.5 s → 7.9 s**, the
`FontMapper` share 60% → 23%.

- **Only a pass is remembered**, keyed on (path, expected hash) and tied to the
  file's size and mtime as they stood both before and after the hash. A failure,
  a font rewritten later (a re-download, the setup flow's offline restore) and a
  font that changed mid-hash are all hashed again. Nothing persists across
  processes. There is no single-flight: chunks that start together before the
  warm thread has finished each hash the fonts once — extra work, never a wrong
  answer.
- **Installed in two places**, both idempotent: the `engine-warm` thread, and
  `routes/translation.py:_load_engine` for a Translate click that beats it.
- **The warm thread pre-checks the fonts on disk** (`preverify_cached_fonts`,
  ~1 s), so the first page doesn't pay that round either. It calls `verify_file`
  on existing files only, never `get_font_and_metadata`, which downloads — the
  same rule that gates the layout-model load. It needs no `babeldoc_core_ready()`
  gate for that reason, and has its own `try`, after the model, so a failure
  never costs the larger warm-up.

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

## Each font is loaded once per process

A `FontMapper` also *loads* all 34 fonts, each into a `pymupdf.Font` that holds
the whole file: 266 MB for any target language, because BabelDOC's
`__add_fallback_to_font_family` appends every other family's fonts to each one.
At 6 mappers a page (7 on the LLM path) that is 1.6 GB of fonts a page, and
BabelDOC never gave it back on time. The constructor memoizes by wrapping bound
methods in `lru_cache` — its own `has_char`/`map_in_type`, and every font's
`has_glyph`/`char_lengths` — so each mapper and each font sits in a reference
cycle that only CPython's full collection frees. Full collections are scheduled
by counts of objects, which font buffers barely move, and nothing in BabelDOC,
Argos or PDFusion calls `gc.collect()`.

On a long document that was an out-of-memory kill, whatever the translator. A
346-page book on Argos (2 pages in flight) took the sidecar to 12 GB, and the
kernel killed it 238 pages in. Replayed through the real pipeline, four pages
took the process from 0.26 GB to 6.5 GB with no full collection in between; the
LLM path's 6 pages in flight (`cpu // 2` on 12 cores; a stub translator that
returns its input, so no request was made) passed 6.5 GB before its first page
finished. Two things about the numbers in BabelDOC's log: "Peak memory usage" is
the whole sidecar and its children, not one sub-job, and on Linux it
over-counts. Between its two-second PSS samples it adds up the RSS of the
sidecar and of each forked PDF-save child, and a forked child's RSS includes
every page it shares with the sidecar.

`processors/font_cache.py` replaces `FontMapper.__init__` with a copy of
BabelDOC's whose font loop takes each font from a process-wide cache, which gives
a font BabelDOC's per-font attributes and memos once, at its first load. On the
same book afterwards, its 301 pages with text went through in 23 minutes with
the sidecar never above 2.4 GB — 1,806 mappers sharing one set of fonts — and a
page translated before and after the change came out the same, every word in
the same place. Memory still drifted up afterwards, by +1.25 MB a page past a
~40-page warm-up. It was not garbage waiting for a full collection, which was
the first guess: 29 full collections ran on their own over 129 pages, and a
forced one at the end freed next to nothing. It had two causes:
- PyMuPDF leaking ~1,690 objects a page in the rolling-PDF rebuild. See the
  next section.
- glibc keeping ~410 MB of freed heap. See "glibc's heap is trimmed after
  every page".

- **Sharing is safe because nothing writes to a font after construction.** The
  stages only read a mapper (`has_char`, `map`, `fontid2font`, `base_font`,
  `add_font`), and PyMuPDF never releases the GIL — neither `_mupdf.so` nor
  `_extra.so` imports `PyEval_SaveThread` — so MuPDF calls from different pages
  run one at a time. Python does run inside a call, in PyMuPDF's warning and
  error callbacks, where threads can switch: the same exposure as the one MuPDF
  context every page already shares. The shared memos are keyed by a character
  (and, for `char_lengths`, a font size) and capped at 10,240 entries each;
  `char_lengths` hands every caller the same list, which they only read.
- **The fonts stay.** ~266 MB is resident from the first translation until the
  sidecar exits. The cache is never pruned: every mapper wants all 34.
- **It is a copy of BabelDOC 0.5.24's constructor**, so `install()` patches only
  a BabelDOC whose version is in `VERIFIED_BABELDOC`; any other keeps its own
  constructor and logs a warning. babeldoc is pinned to a range, and a copy that
  no longer matches could break every stage, where an unpatched one only costs
  the memory. `test_font_cache.py` compares the copy with the installed
  constructor attribute by attribute, and fails by name on an unverified
  version: re-check the copy against `fontmap.py` and extend the set when
  bumping babeldoc. Re-check the GIL claim above when bumping PyMuPDF (last
  checked on 1.25.5 and 1.27.2.3).
- **Installed by `routes/translation.py:_load_engine`**, which every job runs
  before BabelDOC builds anything. The class is patched, not the module's name
  for it: each stage imported `FontMapper` by name.
- **A failed load is not remembered**, so a font BabelDOC re-downloads is loaded
  by the next mapper.
- **The shared memos fill slowly and stop.** Over 129 pages the 34 fonts held
  ~8,300 `has_glyph` / `char_lengths` entries, out of a 696,320 cap. That is
  the few thousand lists that still appear on PyMuPDF 1.27. They are not a
  leak, so don't chase them.

## PyMuPDF is at least 1.27.2

Before 1.27.2, PyMuPDF's C++ helper behind `annot_xrefs`
(`_extra.JM_get_annot_xref_list`) leaks a list on every call. `Link.next`
calls it twice per link, so `get_links()` on a page with 23 links leaked 46
lists. `insert_pdf` copies every page's links through it.

`rebuild_rolling_pdf` re-inserts the *whole* document after every page, so
each rebuild leaked one list per call for every link in the book: ~2,120 lists,
~150 KB, on the 346-page book. That is O(pages) per page and O(pages²) for a
whole document. The lists carry one extra reference and no referrer, so gc
never sees them as garbage.
- 1.25.5 and 1.26.7 leak. 1.27.2.3 and 1.28.2 don't.
- Measured after warm-up: +1.25 MB/page on 1.25.5, +0.18 on 1.27.2.3, and
  live objects flat.
- `test_rebuilding_the_rolling_pdf_over_and_over_leaves_no_objects_behind`
  guards it. Pages without links never leaked, which is why the older rebuild
  tests could not catch it.
- `<1.28` because 1.28 deprecates the `fitz` name every module imports.

What else the bump changed:
- `TEXT_DEHYPHENATE` is ignored, so chat joins line-break hyphens itself.
  See data-and-chat.md.
- `find_tables` reads more as tables. It turns ResNet's architecture figure
  into half-empty two-column grids that the ingestor's mostly-empty guard lets
  through, which costs one benchmark question.
- `find_tables` prints a one-line `pymupdf_layout` notice to stdout once per
  process. The shell drains stdout into shell.log.
- The translation output was checked over 129 pages: it differs from 1.25.5
  no more than two runs of 1.25.5 differ from each other. Argos's batching
  depends on timing.
- Speed is unchanged: 4.33/4.57 s a page before, 4.47/4.38 after. That was
  A-B-B-A, 46 pages each, and measured together with the trim below.

## Page ranges

A translation covers the pages the toolbar's Pages box names (#33) — `1-20, 35`,
blank for all of them. There is **no limit** on how many pages, or how large a
file: `translation.max_pages` (50) and `max_file_size_mb` (50) existed until
then, capped at 100 pages and 200 MB, and are gone. Their cost was real —
BabelDOC takes ~6 s a page and the rolling PDF is rewritten after every chunk —
but it is time, which the user can read off the progress overlay and cancel,
not a reason to refuse. A `config.toml` that still holds either key loads, and
the next save drops it; `PUT /config` answers 422 to them.

- **One check, made twice.** `page_selection.selected_pages` is what
  `POST /translate` runs before creating the job (off the event loop, since it
  opens the PDF — and `page_selection.py` is stdlib-only, while
  `pdf_pages.py` imports `fitz` inside each function, because the pre-flight
  is on the request path) and what `PDFProcessor._validate_file` runs again
  inside it. It refuses an empty selection, a reversed range and a page past
  the end of the document.
- **The output is always the whole document.** Only the selected pages are
  split into chunks, but `rolling_segments` fills every other page from the
  original, so the viewer's page count never changes and pages outside the
  selection stay readable. Neighbouring original pages are inserted as one run.
  The rebuild is cheap next to BabelDOC's ~6 s per page: measured at 0.4 s per
  rebuild for a 300-page, 77 MB PDF (0.23 s with a 10-page selection). It is
  per chunk, though, and it writes a full copy each time, so a very large file
  costs disk (two copies are kept while a run goes) and a little more per page
  than a small one.
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
   pipeline**, link the cached PDF into the live output dir, and emit synthetic
   SSE `progress`/`done` events. LRU-evicted to `pdf_cache_max_size_mb` (default
   1000 MB). Bump `PIPELINE_VERSION` when any BabelDOC config field that changes
   output (font, watermark mode, etc.) changes, or how that output is
   post-processed before it is cached (v2: fonts subset per chunk).

   **An entry and the job output it came from are one file under two names.**
   Both directions — `store()` and materializing a hit — go through
   `pdf_cache.link_or_copy`, a hard link staged at `.tmp` and `os.replace`d,
   falling back to a copy where links are refused (`EXDEV` from a tmpfs
   `/tmp`, FAT/exFAT, some network shares). That saves one PDF of disk and
   the copy per translation. Removing or replacing either name is safe — the
   next job's rmtree, an LRU eviction, a new file `os.replace`d over it — but
   **nothing may rewrite an artifact in place** once it exists, or the write
   reaches the other name too. That holds today because every rolling
   version and every job dir is a fresh name, and anything that rewrites a
   PDF (`subset_embedded_fonts`, `export_pdf`) stages a new file and renames
   it. Two side effects: on Windows, an eviction while the viewer is
   streaming the same bytes from the job's name can fail its unlink (a
   warning; the row is gone and the next store of that key replaces the
   file); and an evicted entry's space comes back only once the job's name
   is gone too, at the next job.
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
