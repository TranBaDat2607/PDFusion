# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PDFusion is a Windows desktop app for translating PDFs (default target: Vietnamese) while preserving layout/formatting. It uses BabelDOC as the translation engine and integrates an optional RAG (Retrieval-Augmented Generation) chat for asking questions about the loaded document.

The UI was migrated from PySide6/qfluentwidgets to **Tauri (Rust shell) + React + TypeScript + Tailwind + shadcn/ui** in 2026. The Python translation/RAG/config/utils modules are unchanged — they're now exposed as a **FastAPI sidecar** that the Tauri shell spawns at app startup.

## Running the Application

```bash
# Full desktop app (Tauri shell auto-spawns the sidecar):
cd desktop
pnpm tauri dev          # dev with HMR
pnpm tauri build        # production installer (.msi / .exe in src-tauri/target/release/bundle/)

# Frontend-only (React in browser, no Rust shell, no sidecar):
cd desktop
pnpm dev                # vite dev server
pnpm build              # tsc + vite build → desktop/dist/

# Sidecar only (for backend debugging):
conda activate pdfusion
python main.py          # equivalent to: pdfusion-sidecar (console script from pyproject)
# → prints `READY port=<n> token=<n>` on stdout; OpenAPI docs at http://127.0.0.1:<n>/docs
```

> The local conda env is named `pdfusion` (single `f`). The Tauri shell looks
> for `~/anaconda3/envs/pdfusion/python.exe` by default; override with the
> `PDFUSION_PYTHON` environment variable if your env lives elsewhere.

**External system dependencies:**
- Ghostscript (optional — only needed by Camelot for table extraction during RAG indexing; pdfplumber fallback runs without it)
- WebView2 Runtime (ships with Windows 11)
- Rust toolchain (rustup + cargo, `stable-x86_64-pc-windows-msvc`) — required to build/run the Tauri shell (`cargo check` / `pnpm tauri dev` / `pnpm tauri build`)
- MSVC Build Tools 2022/2026 (Rust's linker on Windows)

**Environment setup:**
```bash
conda create -n pdfusion python=3.11.14
conda activate pdfusion
pip install -r requirements.txt        # canonical install — pins all RAG + advanced deps
# Alternative: pip install -e ".[rag,advanced]"  (extras live in pyproject.toml)

cd desktop
pnpm install
```

> If bare `pnpm` isn't resolvable even after `corepack enable` (it can fail
> with `EPERM` writing shims into `Program Files\nodejs` without admin rights),
> install it globally instead: `npm install -g pnpm`.

> Note: `requirements.txt` and `pyproject.toml` are **not** kept in lockstep.
> `requirements.txt` flatly installs the RAG + advanced extras (chromadb,
> langchain, camelot, pytesseract, etc.); `pyproject.toml` puts those behind
> `[project.optional-dependencies]` named `rag`, `advanced`, `all`. For the
> desktop app to fully work (RAG chat especially), install everything via
> `requirements.txt` or `pip install -e ".[all]"`.

**API key configuration** — create a `.env` in the project root:
```
OPENAI_API_KEY=...
GEMINI_API_KEY=...
ANTHROPIC_API_KEY=...    # optional
```
Or use the in-app Settings sheet — keys are encrypted via `utils/encryption.py`
before being written to `~/AppData/Local/PDFusion/config.toml`. On Windows that
is **DPAPI** (`CryptProtectData`, user-scoped, with app entropy); values written
by the older MachineGuid-derived Fernet scheme still decrypt and are upgraded on
the next save, so nobody re-enters a key. `config.toml` is written to a temp file
and `os.replace`d into position — an in-place write that crashed used to truncate
the file, and a truncated config loads as defaults, i.e. silently discards every
setting including the keys. The outgoing generation is kept as `config.toml.bak`
**with the API keys stripped out** (`manager.py:_write_backup`): a backup must
never be more readable than the file it backs up, and holding that as an
unconditional invariant is what avoids having to detect the one migration
(legacy → DPAPI) where copying verbatim would have parked a machine-readable key
beside the hardened one. A key is re-enterable; the rest of the file is what is
worth recovering by hand.

## Architecture

### Two-process model

```
┌─────────────────────────────────────────────────────────┐
│ Tauri shell (Rust) — desktop/src-tauri/                 │
│  • Spawns + supervises Python sidecar at startup        │
│  • Kills sidecar on app exit (RunEvent::ExitRequested)  │
│  • Exposes `sidecar_info` command to the React side     │
│  • Native dialogs (open/save), open/reveal a PDF        │
│                                                         │
│  ┌──────────────────────────────────────────────────┐   │
│  │ WebView2: React + Vite + Tailwind + shadcn/ui    │   │
│  │  • TanStack Query (server state)                 │   │
│  │  • Zustand (client UI state)                     │   │
│  │  • pdf.js (client-side PDF rendering)            │   │
│  │  • react-markdown + KaTeX + shiki (chat output)  │   │
│  │  • motion (chat bubble animations)               │   │
│  │  • Bearer-token HTTP/SSE → 127.0.0.1:<port>      │   │
│  └──────────────────────────────────────────────────┘   │
│                       │                                 │
│  ┌────────────────────▼─────────────────────────────┐   │
│  │ FastAPI sidecar — src/desktop_pdf_translator/api │   │
│  │  • uvicorn on 127.0.0.1:<ephemeral port>         │   │
│  │  • Bearer-token auth (URL-safe 32-byte secret)   │   │
│  │  • SSE streams for translate / index / ask jobs  │   │
│  │  • Wraps existing PDFProcessor / RAG / Config    │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

The sidecar prints **one** handshake line at startup that the Rust shell parses:
```
READY port=54213 token=Yd7Hf...G3
```
The token is then forwarded to the webview via the `sidecar://ready` Tauri event and used as the `Authorization: Bearer` header on every fetch from the React side.

### Module layout

| Path | Responsibility |
|---|---|
| `desktop/src-tauri/src/main.rs` | Tauri entry; defers to `desktop_lib::run()` |
| `desktop/src-tauri/src/lib.rs` | Builder + plugins + sidecar spawn on setup + shutdown hook |
| `desktop/src-tauri/src/sidecar.rs` | Python locate, child process, READY parsing, health-poll |
| `desktop/src/App.tsx` | Shell: ThemeProvider → QueryClientProvider → Workspace |
| `desktop/src/components/layout/` | `Header`, `ContextBar`, `MainLayout` (resizable splits) |
| `desktop/src/components/pdf-viewer/` | `PdfViewer` (pdf.js, lazy render, zoom/fit) |
| `desktop/src/components/chat/` | `ChatPanel`, `UserMessage`, `AssistantMessage`, `ActionLog`, `ReferenceList`, `ChatInput` |
| `desktop/src/components/settings/` | `SettingsSheet` (tabs per service) |
| `desktop/src/components/translation/` | `ProgressOverlay`, `TranslatedFileActions` (Save / Open / Show in folder) |
| `desktop/src/components/ui/` | shadcn-generated primitives (button, dialog, sheet, …) |
| `desktop/src/lib/api-client.ts` | Typed HTTP wrapper with bearer-token + sidecar URL helpers |
| `desktop/src/lib/sse.ts` | Authenticated SSE reader (native EventSource can't set headers) |
| `desktop/src/lib/store.ts` | Zustand store for UI state |
| `desktop/src/lib/export-pdf.ts` | Pure save-flow logic + path helpers (deps injected, so it's unit-testable) |
| `desktop/src/hooks/` | `useSidecar`, `useConfig`, `useTranslation`, `useRagIndex`, `useRagAsk`, `useExportTranslated` |
| `src/desktop_pdf_translator/api/server.py` | FastAPI app + uvicorn entry + port discovery |
| `src/desktop_pdf_translator/api/auth.py` | Bearer-token middleware |
| `src/desktop_pdf_translator/api/jobs.py` | In-memory job registry + asyncio.Queue per job for SSE |
| `src/desktop_pdf_translator/api/routes/*` | `config`, `translation`, `rag`, `pdf` route modules |
| `src/desktop_pdf_translator/config/` | `ConfigManager` + Pydantic `AppSettings` (unchanged) |
| `src/desktop_pdf_translator/processors/` | `PDFProcessor` async generator wrapping BabelDOC (unchanged) |
| `src/desktop_pdf_translator/translators/` | `BaseTranslator`, OpenAI/Gemini/Anthropic/Argos + `TranslatorFactory` |
| `src/desktop_pdf_translator/rag/` | ChromaDB + `EnhancedRAGChain` (deep-search/web-research was dropped in `35bca2c`) |
| `src/desktop_pdf_translator/utils/` | API key encryption; `file_export.py` (durable copy of a translated PDF) |
| `src/desktop_pdf_translator/translators/translation_cache.py` | Persistent **paragraph-level** SQLite cache (singleton `get_translation_cache()`) |
| `src/desktop_pdf_translator/processors/pdf_cache.py` | Persistent **whole-PDF** SQLite cache (singleton `get_pdf_cache()`) |

### HTTP API (sidecar)

All routes (except `GET /health`) require `Authorization: Bearer <token>`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness probe (no auth) |
| GET | `/auth/ping` | Auth probe — used by the Rust shell after startup |
| GET | `/config` | Current settings (API keys masked) |
| PUT | `/config` | Update API keys / models / language defaults |
| POST | `/config/validate` | Test a key by spinning up a translator + calling its `validate_configuration()` |
| GET | `/config/options` | Static dropdown data (languages, services, models) + `supported_pairs` per service (`null` = unrestricted) |
| GET | `/config/cache` | Paragraph-cache stats (entries, hit rate, size) |
| DELETE | `/config/cache?scope=all\|expired` | Clear/GC the paragraph-level translation cache |
| POST | `/translate` | Start translation job → returns `{ job_id }`. `source_lang` / `target_lang` / `service` are `None`-defaulted (config applies); an unsupported pair is refused with **422** before the job is created. `bypass_cache: bool` forces a full re-translate (used by the "Re-translate" button). There is deliberately **no `output_dir`** — output always lands in a per-job `%TEMP%` dir that the cleanup paths know about |
| GET | `/translate/{job_id}/events` | SSE: `progress`, `chunk_ready`, `paragraph_translated`, `done`, `error`, `cancelled`. **`chunk_ready` arrives in priority order, not page order** — nearest the viewer's page first — so `chunk_index` is not a completion count and `pages_in_chunk[1]` is not a running total. Accumulate with `lib/translation-progress.ts`; page totals come from `total_pages` (`total_chunks` is not a page count — Argos runs 3-page chunks) |
| POST | `/translate/{job_id}/cancel` | Cancel an in-flight translation |
| POST | `/rag/index` | Index a PDF into ChromaDB → returns `{ job_id }` |
| GET | `/rag/index/{job_id}/events` | SSE: `progress`, `done`, `error` |
| POST | `/rag/ask` | Ask the RAG chain → returns `{ job_id }` |
| GET | `/rag/ask/{job_id}/events` | SSE: `progress`, `answer`, `done`, `error`. Retrieved chunks ride on `answer.pdf_references` (**not** `pdf_sources`); their `page` is **1-indexed**, or `null` when the chunk has none. Chunk metadata is 0-indexed and `PdfViewer.scrollToPage` counts from 1, so `rag_chain._display_page` converts at that one boundary |
| DELETE | `/rag/document/{document_id}` | Remove an indexed document from the vector store |
| GET | `/pdf/file?path=...` | Stream a PDF from disk (used by pdf.js client-side) |
| POST | `/pdf/export` | Copy a translated PDF to a user-chosen permanent path (`{source_path, destination_path, protect_path?}` → `{saved_path, bytes_written}`). `protect_path` is the opened document; it's refused as a destination |

### Long-running jobs (SSE pattern)

Long-running endpoints (translate, index, ask) follow the same pattern:
1. `POST /resource` returns `{ job_id }` immediately.
2. The actual work runs in a background asyncio task that pushes events into an `asyncio.Queue` keyed by `job_id`.
3. `GET /resource/{job_id}/events` opens an SSE stream that drains that queue.
4. A terminal event (`done`, `error`, or `cancelled`) closes the stream.

This replaces the previous `QThread + new asyncio loop` pattern from the PySide6 GUI.

### Translation output lifecycle — nothing the pipeline writes is permanent

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
   drain runs `cleanup_partial_artifacts()`, which unlinks all but the newest
   rolling PDF. So `TranslationState.status` has a `cancelling` state covering
   the window until the terminal SSE event. Anything that touches the artifact
   must gate on `isTranslationBusy()`, never on `status === "running"`.
3. **`export_pdf(protect=...)` refuses to overwrite the opened document.** The
   Save dialog lets the user type their source document's own name and confirm
   "Replace?", which would destroy their input with no undo. Relatedly,
   `suggestedExportName` *always* appends `_<lang>` — `paper_vi.pdf` →
   `paper_vi_vi.pdf` — so the suggestion can never collide with the source.
   Skipping the suffix when the stem "looks translated" guesses intent from a
   filename (`chapter_vi.pdf` is a Roman numeral) and manufactures exactly that
   collision.

### Language selection and backend capabilities

The toolbar's From/To selection reaches the pipeline through the request body,
not through config. `useTranslation.start()` sends `source_lang` / `target_lang`
/ `service` explicitly (built by `lib/translate-request.ts`), because
`ContextBar`'s `update.mutate` is async: a Translate click landing before that
PUT would otherwise run the *previous* selection. `CompletionEvent.target_lang`
still reports the language `process_pdf` actually resolved, and the store's
`translationTargetLang` records it, so the Save dialog names the file after what
was produced rather than what was asked for.

**`None` is the only way to say "unspecified".** `TranslateRequest` /
`PrewarmRequest` default both language fields to `None`; the configured default
is applied in exactly one place,
`translators/capabilities.py:resolve_languages`. Restoring a non-null default
on those fields (`LanguageCode.AUTO` / `VIETNAMESE`) is what caused issue #12 —
both sentinels are truthy, so `process_pdf`'s `source_lang or settings…`
fallback became dead code and every run produced Vietnamese. `AUTO` is a real,
user-selectable source language, never a stand-in for "not chosen".

**`translators/capabilities.py` is the single source of truth** for which
requests can run, and is kept free of heavyweight imports (no BabelDOC, no
torch, no SDK clients) so the API layer and tests can consult it cheaply:

- `SUPPORTED_PAIRS` — `None` means unrestricted (the LLMs prompt for any target
  via `LANGUAGE_DISPLAY_NAMES`); a `set` is exhaustive. Argos declares
  `{("en","vi")}` and `argos_translator.py` reads its own `_SUPPORTED_PAIRS`
  from here, so a request cannot be accepted as valid and then rejected mid-run.
- `resolve_effective_service` — the "LLM with no API key silently becomes
  Argos" rule, previously duplicated in `PDFProcessor` and the prewarm route.
- `POST /translate` pre-flights the pair and returns **422** before creating the
  job, checked against the *effective* service. Without this, honoring the
  target language would trade a silent wrong-output for a `ValueError` raised
  minutes into a run, from inside `translate()`, with a partial artifact on disk.
- `GET /config/options` exposes `supported_pairs` per service, with auto-source
  aliases already expanded, so the toolbar greys out unreachable targets without
  reimplementing the matrix in TypeScript.

Two consequences worth remembering: the frontend sends the **requested** service
(not the effective one) so the sidecar still emits its "falling back to Argos"
notice; and a disabled Radix `SelectItem` sets `pointer-events: none`, so the
"why" is rendered as inline text plus a footer note, never a hover tooltip.

Broadening Argos beyond en→vi means shipping more language packs, not editing
`SUPPORTED_PAIRS` alone.

### Two-tier translation caching

Two independent, persistent SQLite caches sit on the translation path. Both live
under `~/AppData/Local/PDFusion/`, use WAL + per-thread connections, are
process-wide singletons, and are content-addressed by SHA-256 — so neither is
invalidated by re-runs with identical inputs.

1. **Whole-PDF cache** (`processors/pdf_cache.py`, `get_pdf_cache()`). Keyed on
   `sha256(file_bytes) | lang_in | lang_out | service | model | PIPELINE_VERSION`.
   Source language *was* excluded, on the premise that it never changes output;
   that only held while the API pinned every request to `auto`. The LLM system
   prompts name the source explicitly, so `auto` and `en` must not collide —
   see the comment at `_make_cache_key`. A hit lets `process_pdf` **skip the entire BabelDOC
   pipeline**, copy the cached PDF into the live output dir, and emit synthetic
   SSE `progress`/`done` events. LRU-evicted to `pdf_cache_max_size_mb` (default
   1000 MB). Bump `PIPELINE_VERSION` when any BabelDOC config field that changes
   output (font, watermark mode, etc.) changes.
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

### Translator failures are counted, not swallowed

`BaseTranslator._handle_translation_error` is the funnel every backend's
`except` clause routes into, and it still returns the **source text** — raising
would change nothing, because BabelDOC's `ILTranslator` catches whatever
`translate()` raises and continues ("ignore error and continue"). So a bad key,
a 429 or a network blip cannot fail a job on its own; the document just comes
out partly untranslated while the pipeline reports success.

**Every path that hands back source text goes through that funnel — including
the ones that never raise.** That is the invariant a new backend has to
preserve, and the one that was missed first time round: a bare `return text`
is invisible to the counter, so the run still reports "complete" and still gets
cached. The non-exception cases are "the provider returned nothing usable"
(a Gemini safety filter drops `candidates` entirely; Anthropic/OpenAI can
return empty content), and, in Argos, a failed `_ensure_en_vi_installed()`
(the ~80 MB pack download — it raises for *every* paragraph, so an unguarded
call meant a first run with no network produced a whole untranslated document
that looked finished), a batch-wait timeout, and an empty result slot.

Three things close that gap:

- **Counting.** `BaseTranslator.failed_translations`, plus an
  `on_translation_failed(error, fatal)` kwarg threaded through the same way as
  `on_paragraph_translated`. `CompletionEvent` carries `failed_paragraphs` /
  `total_paragraphs`, and the overlay says "N of M paragraphs could not be
  translated" with a Retry (which is Re-translate — `bypass_cache=true`).
  The **M** is `translator.translate_call_count`, not the processor's
  `_paragraphs_seen`: the latter is the live-ticker's counter and skips any
  paragraph whose preview came out empty, which understates the denominator.
  Both counters increment under `BaseTranslator._counter_lock` — one instance
  serves BabelDOC's whole worker pool, and `+= 1` is a read-modify-write.
- **Not caching such a run** (see `is_cacheable_artifact` above).
- **Aborting on a fatal error.** `translators/base.py:is_fatal_translation_error`
  classifies 401/403 (duck-typed on `status_code`/`code`/`.response`, with a
  message fallback for google-genai, which carries the code only in its text).
  On the first fatal failure `PDFProcessor._handle_translation_failure` pushes a
  wake-up item onto the multiplexer queue — the loop is parked on
  `events_queue.get()`, so without it the job would sit there while every
  remaining paragraph made the same doomed call — and the loop raises
  `TranslationProcessError`, which `_process_with_babeldoc` re-raises unwrapped
  so its user-facing sentence survives.

  The message-marker fallback names an **API key** specifically. Generic
  phrases (`unauthorized`, `permission denied`) were tried and removed: a
  provider that means them also sends 401/403, which the status check already
  catches, while `PermissionError`/`OSError` carry "Permission denied" in their
  text and would abort a run whose credentials were fine. Argos reaches this
  path too (a 403 from the package index is fatal, and correctly so), which is
  why the sentence comes from `base.py:describe_fatal_error` — it has no API
  key, so the LLM wording would send the user to check a credential they never
  set and to "switch to Argos" while already on it.

Relatedly, `PUT /config` **only auto-promotes `preferred_service` off Argos
when the new key validates** (one provider round-trip, only on that path). An
explicit `preferred_service` in the payload is always honoured — that's the
user's own choice. Promoting on a typo'd key used to move the user from a
working offline translator onto one that fails every paragraph silently.

Two things that probe has to keep: it runs under `_VALIDATE_PROBE_TIMEOUT_S`
(Gemini's `validate_configuration` passes no timeout of its own, and this is
the *save* path now, not just the Validate button), and a timeout reports the
same "don't promote" as a rejection. The frontend's warning toast in
`useConfig.ts` picks the provider to name using the **same priority order** the
server promotes by — one PUT can carry several keys, and only the first is
probed, so the two orders must not drift apart.

### Translator plug-in interface (BabelDOC integration)

BabelDOC drives chunking, layout, and PDF reassembly; it delegates the actual text translation to a translator object passed into `BabelDOCConfig(translator=...)` (see `processors/processor.py:364`). Two important facts about this seam:

1. **Chunking unit = paragraph**, not page. BabelDOC's `ParagraphFinder` groups characters into `PdfParagraph` objects (one body paragraph, heading, caption, list item, etc.), then `ILTranslator.translate_paragraph` issues **one `translate()` call per paragraph** in a thread pool. A typical 10-page paper → dozens to hundreds of small calls, parallelized. Throughput is gated by `qps=4` and `pool_max_workers` in `processor.py:_create_babeldoc_config`.

2. **The interface is duck-typed, not nominal.** The project's `OpenAITranslator` / `GeminiTranslator` / `AnthropicTranslator` / `ArgosTranslator` (`translators/*.py`) inherit from the project's *own* `translators/base.py:BaseTranslator`, **not** from `babeldoc.translator.translator.BaseTranslator`. BabelDOC accepts any object that implements:

   - `translate(text: str) -> str` — main entrypoint
   - `get_formular_placeholder(id) -> (placeholder, regex)` — formula preservation
   - `get_rich_text_left_placeholder(id)` / `get_rich_text_right_placeholder(id)` — rich-text span tags
   - `restore_formular_placeholder(text, id, original)` — post-processing
   - attributes `lang_in`, `lang_out`

   To add another backend (Google Translate, Helsinki opus-mt, NLLB, …), follow the same shape as `translators/openai_translator.py` and register it in `TranslatorFactory._translators` (`translators/factory.py:22`). The bundled BabelDOC ships only an OpenAI-compatible translator — no built-in Google/DeepL.

3. **Argos is the default offline backend.** `translators/argos_translator.py` is a free, no-API-key NMT translator used when no LLM key is configured. Important quirks:
   - **MVP supports en→vi only.** Other language pairs raise `ValueError` directing the user to switch source language or use an LLM. Update `_SUPPORTED_PAIRS` to broaden support.
   - **Lazy install.** The `argostranslate` package is imported lazily and the ~80 MB en→vi language pack is downloaded on first `translate()` call, guarded by a `threading.Lock`. Sidecar startup is unaffected.
   - **Caching.** Argos is deterministic, so it benefits from both the persistent paragraph cache (`translation_cache.py`) and the in-process batch coalescing added in `3356f30` (concurrent `translate()` calls are coalesced into batches of 4). Its `model` field is the fixed string `"argostranslate"`.

### React state ownership

- **TanStack Query** owns all server state (`useConfig`, `useOptions`).
- **Zustand store** (`lib/store.ts`) owns ephemeral UI state: current PDF paths, active job ID, RAG enabled flag, chat drawer open/closed.
- **Job hooks** (`useTranslation`, `useRagIndex`, `useRagAsk`) own per-stream local state and update the global store on terminal events.

### UI conventions

- **shadcn/ui** is the component baseline. Add new components with `pnpm dlx shadcn@latest add <name>`.
- **Tailwind v4** with the design tokens defined in `desktop/src/index.css` via `@theme inline`. The accent color is the original PDFusion green (`oklch(0.689 0.179 142.51)` ≈ `#4CAF50`).
- **Theme**: `next-themes`-style `ThemeProvider` toggles a `dark` class on `<html>`. Default is `system`.
- **Icons**: `lucide-react` everywhere.
- **Animations**: `motion` (formerly Framer Motion) for chat message entrance.
- **Markdown / LaTeX / code in chat answers**: `react-markdown` + `remark-gfm` + `remark-math` + `rehype-katex`. There's no PNG fallback for formulas — KaTeX renders client-side.
- **Toasts**: `sonner` via `<Toaster />` mounted in `App.tsx`. Use `toast.success(...)` / `toast.error(...)` from anywhere.
- **Dialogs**: shadcn `<Dialog>` for confirmations, `<Sheet>` for the settings panel.

## Configuration

- Runtime config: `~/AppData/Local/PDFusion/config.toml` (encrypted API keys).
- Defaults / reference: `config/default_config.toml`.
- `.env` is auto-loaded via `python-dotenv` and overrides the TOML. It's searched at the **repo root** (resolved from `__file__`, not `cwd` — `cwd` is non-writable `C:\Program Files\…` on an installed launch) and in the AppData config dir. See `config/manager.py:_load_dotenv`.
- Singleton: `get_config_manager()` / `get_settings()` from `desktop_pdf_translator.config`.
- Cache-related settings live under `[translation]` in `AppSettings` (`config/models.py`): `cache_translations` (paragraph cache, default on), `cache_translated_pdfs` (whole-PDF cache, default on), `pdf_cache_max_size_mb` (LRU cap, default 1000). Changing `pdf_cache_max_size_mb` applies without a sidecar restart (re-read on every eviction pass).

## Tauri shell details

- **Plugins enabled**: `opener` (open external URLs) and `dialog` (file picker) — that's all. `shell` and `fs` were registered but never imported by `desktop/src`; PDFs reach the viewer over HTTP from the sidecar, and Save/Open/Reveal go through the app commands in `lib.rs`. Don't re-add a plugin "just in case": every one widens what an injected script can invoke. Same reasoning inside a plugin: the capability grants `opener:allow-open-url` + `opener:allow-default-urls` rather than `opener:default`, because that set also carries `allow-reveal-item-in-dir` — a second, unvalidated route to the reveal that `reveal_path_in_file_manager` exists to gate. The app commands call the plugin's **Rust** API (`app.opener()`), which capabilities don't apply to, so narrowing the webview's grant costs nothing.
- **Window**: 1400×900 default, min 1024×700. `withGlobalTauri` is off — `__TAURI_INTERNALS__` (which `lib/tauri-ready.ts` waits on) is injected regardless; the flag only adds the legacy `window.__TAURI__` global.
- **CSP**: set in `tauri.conf.json` — `default-src 'self'` with `connect-src` widened to `http://127.0.0.1:*` (the sidecar) plus Tauri's IPC origin, `worker-src blob:` (pdf.js), and `style-src 'unsafe-inline'` (Tailwind's runtime styles). Tauri nonces its own init script, so `script-src` stays at `'self'`. It applies to the bundled app only — in `pnpm tauri dev` the page is served by Vite, which Tauri doesn't inject headers into, so **a CSP break shows up first in `pnpm tauri build`**, not in dev.
- **CORS / dev origins**: the sidecar's allowlist (`server.py:_allowed_origins`) is exactly Tauri's custom-protocol origins, plus Vite's `localhost:1420` **only in dev**. Which one applies is a two-sided handshake: the shell sets `PDFUSION_DEV_ORIGINS` to `"1"`/`"0"` from `cfg!(debug_assertions)` (`sidecar.rs:dev_origins_flag`), on both spawn paths. The sidecar must **not** decide this from `sys.frozen` alone — frozen means "PyInstaller built it", not "shipped app", and discovery prefers a staged `binaries/*.exe` over local Python, so after `build-sidecar.ps1` a `pnpm tauri dev` run pairs a *frozen* sidecar with a *Vite-hosted* webview. Getting that wrong rejects every request the app makes (preflights → `400 Disallowed CORS origin`) and looks exactly like the sidecar failing to start. `sys.frozen` remains the fallback for a sidecar started without the shell. Don't swap `debug_assertions` for `tauri::is_dev()`: that's `!cfg!(feature = "custom-protocol")` and this crate declares no `[features]`, so it's `true` even in a release bundle.
- **Sidecar lifecycle** is wired in `lib.rs::run()`'s `setup` and the `RunEvent::ExitRequested` handler kills the child process.
- **Sidecar cwd & writable paths**: the child is spawned with cwd = `%LOCALAPPDATA%\PDFusion\` (`sidecar::appdata_dir`), **not** the install dir (`C:\Program Files\PDFusion\` is read-only for non-admins → `WinError 5` on any relative-path write). `lib.rs::setup` pre-creates the AppData subdir layout (`sidecar::ensure_appdata_layout`) before spawn so Python subsystems don't race on first-run `mkdir`.
- **Per-job translation output** is a throwaway `%TEMP%\pdfusion-translate-<rand>\` dir (not a persistent `translated_pdfs/`). It's wiped three ways: by the next job, by the Tauri `ExitRequested` handler (`sidecar::cleanup_translate_temp_dirs`), and by the FastAPI lifespan orphan sweep on sidecar startup (`server.py:_sweep_orphan_translate_dirs`, only dirs older than 1h). Persistent translated PDFs live in the whole-PDF cache instead.
- **Sidecar discovery** order (see `desktop/src-tauri/src/sidecar.rs`):
  1. **Bundled exe** — `pdfusion-sidecar-<triple>.exe` resolved via `BaseDirectory::Resource`. This is what end users hit (shipped via `bundle.externalBin` in `tauri.conf.json`).
  2. **Dev fallback** — Python interpreter chain: `PDFUSION_PYTHON` env var → `~/anaconda3/envs/pdfusion/python.exe` → `~/miniconda3/envs/pdfusion/python.exe` → `python` on PATH, then `python -m desktop_pdf_translator.api.server` with `PYTHONPATH=<root>/src`.

## Building the desktop installer

```powershell
# 1. Build the standalone sidecar (PyInstaller, one-dir).
#    Output: dist/pdfusion-sidecar/{pdfusion-sidecar.exe, _internal/}
#    Then staged into desktop/src-tauri/binaries/.
conda activate pdfusion
pip install -e ".[dev]"          # ensures pyinstaller is available
./build-sidecar.ps1

# 2. Build the Tauri installer.
#    tauri.conf.json's beforeBundleCommand also re-runs build-sidecar.ps1 so
#    step 1 is technically optional, but doing it first lets you sanity-check
#    the bundled sidecar in isolation before the slow Tauri bundle step.
cd desktop
pnpm tauri build
# → desktop/src-tauri/target/release/bundle/msi/PDFusion_0.1.0_x64_en-US.msi
```

> **Dev-mode bootstrap caveat**: Tauri's build script validates `externalBin`
> and `resources` paths at *compile time*, so `cargo check`, `pnpm tauri dev`,
> and `pnpm tauri build` all fail on a fresh checkout until the staged sidecar
> exists. If you don't want to wait for the full PyInstaller build just to
> hack on the React/Rust side, run:
>
> ```powershell
> ./build-sidecar.ps1 -Stub
> ```
>
> This drops empty placeholder files into `desktop/src-tauri/binaries/`. The
> Rust shell's sidecar discovery still falls back to your local Python at
> runtime, so `pnpm tauri dev` works exactly like before. Just don't ship the
> stubbed installer — the bundled exe will be zero bytes.

The sidecar is shipped as `externalBin` (the `.exe` next to `pdfusion.exe`)
plus a sibling `_internal/` tree (PyInstaller runtime — Python stdlib +
native .pyd + bundled package data). The `_internal/` tree is staged at
`desktop/src-tauri/_internal/` (not inside `binaries/`) so that Tauri's
`resources` glob installs it at `<install>/_internal/`, sibling to the
renamed `pdfusion-sidecar.exe` — which is what PyInstaller's onedir
bootloader requires to find `python313.dll` et al. First build is slow (~10-20 min) and
the resulting .msi is large (~500 MB-1 GB) because we bundle the full
chromadb + sentence-transformers + babeldoc stack. ML model weights and
the Argos en→vi pack are **not** bundled; they download lazily on first
use to `~/.cache/huggingface` and the argostranslate user dir respectively.

Hidden-import additions for chromadb / babeldoc / etc. live in
`pdfusion-sidecar.spec`. Extend that file (then rerun `build-sidecar.ps1`)
when the bundled exe raises `ModuleNotFoundError` at startup.

## Logs

Two log streams, and they don't meet — worth knowing before hunting for a line
that isn't there:

- **Python sidecar** → `~/AppData/Local/PDFusion/logs/app.log`, but only via
  `main.py::_setup_logging` (the `pdfusion-sidecar` console script, i.e. the
  bundled build or a hand-run `python main.py`). Launched the dev way
  (`python -m desktop_pdf_translator.api.server`, which is what `pnpm tauri dev`
  does), `server.py::main` configures logging to **stderr only** — nothing
  reaches `app.log`.
- **Rust shell** → stderr, always. `lib.rs::run` calls a bare
  `env_logger::try_init()` with no file target, so `log::info!`/`warn!` — including
  the `[sidecar stdout]` / `[sidecar stderr]` relays — land in the `pnpm tauri dev`
  terminal and are **discarded in a release build** (`main.rs` sets
  `windows_subsystem = "windows"`, so there's no console). None of it is in
  `app.log`.

The sidecar's bearer token is `print`ed to stdout, not logged, so it was never in
`app.log`; `sidecar.rs:redact_ready_line` keeps it out of the shell's stderr and
of any file logger added later.

## Tests and code quality

- **Test coverage is narrow — the PDF-export path, the language contract, and
  key storage / config writes.** There is still no suite for the translation
  pipeline proper, RAG, or the cache *storage* layer; if you touch those,
  expect to write tests from scratch.

  ```bash
  # Python (pytest config lives in pyproject.toml; tests/conftest.py puts src/ on sys.path)
  python -m pytest tests           # test_file_export.py, test_pdf_export_api.py,
                                   # test_translate_language_contract.py,
                                   # test_translation_failure_reporting.py,
                                   # test_config_security.py, test_cors_origins.py

  # Frontend (vitest, node environment — no jsdom)
  cd desktop && pnpm test          # src/**/*.test.ts

  # Rust shell (the only Rust tests so far live in sidecar.rs)
  cd desktop/src-tauri && cargo test
  ```

  Importing **anything** under `desktop_pdf_translator.api` costs ~13s: the
  package `__init__` does `from .server import create_app`, and `server.py`
  imports `routes/translation.py` → BabelDOC → torch. Avoiding `create_app()`
  is not enough to dodge it — `test_pdf_export_api.py` mounts `routes/pdf.py`'s
  router on a bare `FastAPI()` and still pays, because it imports `api.auth`.
  `test_cors_origins.py` pays the same toll (it needs the real
  `_allowed_origins`), which is why it's a separate file rather than folded into
  `test_config_security.py` — that one and
  `test_translate_language_contract.py` stay on `config/`, `utils/`,
  `api/schemas.py`, `translators/capabilities.py` and `processors/pdf_cache.py`,
  all pure decisions, and run in a fraction of a second. Keep new tests on that
  side of the line where you can, and don't move a cheap suite onto the
  expensive one. The frontend suite runs in the
  `node` environment: the logic under test takes its Tauri/sidecar
  collaborators as arguments (`lib/export-pdf.ts`) or is pure
  (`lib/translate-request.ts`), so no DOM or testing-library is needed.
- **Python lint/format** tools are declared in `pyproject.toml [project.optional-dependencies].dev` (black line-length 88, isort with black profile, flake8, mypy) but the project has **no** pre-commit, no Makefile, and no CI. Run them manually if you want: `black src/ && isort src/`.
- **TypeScript** is checked by `pnpm build` (which runs `tsc` before `vite build`). There is no separate lint step (no ESLint config).
- **No CI**: `.github/workflows/` does not exist. All checks are local.

## Out of scope (for a later phase)

- **Auto-update** flow.
- **Code signing** for Windows (SmartScreen will warn on first install of the unsigned `.msi`).
- **Cross-platform** (macOS/Linux) — Tauri supports both, but explicit testing deferred. The PyInstaller spec is Windows-tested only.
- **i18n of the UI strings** (the UI itself stays English; the translation *output* follows the toolbar's target language).
- **More Argos language pairs** — the offline backend ships en→vi only. Adding
  a pair means shipping/downloading its pack, then extending `SUPPORTED_PAIRS`
  in `translators/capabilities.py`.
- **Pre-bundled ML assets** (HuggingFace embedding model + Argos en→vi pack) — currently both download on first use. Bundle them later for true offline-first.
- **Auto-save preference** — saving a translation is an explicit action (Save dialog). A "always save `<name>_vi.pdf` beside the source" setting was proposed in issue #11 but deliberately not built: it needs a config field, a Settings control, and an overwrite policy for repeat runs.

## Removed (legacy)

- The old PySide6 / qfluentwidgets GUI in `src/desktop_pdf_translator/gui/` has been deleted along with its deps (`PySide6`, `PySide6-Fluent-Widgets`, `QtAwesome`) from `requirements.txt`. If you need to resurrect the legacy GUI for any reason, pin those three packages back and recover `gui/` from git history (it lived through commit `139d977` "feat: migrate UI from PySide6 to Tauri 2 + React + FastAPI sidecar").
