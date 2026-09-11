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
pnpm tauri build        # production installer (NSIS .exe in src-tauri/target/release/bundle/nsis/)

# Frontend-only (React in browser, no Rust shell, no sidecar):
cd desktop
pnpm dev                # vite dev server
pnpm build              # tsc + vite build → desktop/dist/

# Sidecar only (for backend debugging):
conda activate pdfusion
python main.py          # equivalent to: pdfusion-sidecar (console script from pyproject)
# → prints `READY port=<n> token=<n>` on stdout; OpenAPI docs at http://127.0.0.1:<n>/docs
```

> The examples above name the env `pdfusion`; the Tauri shell also auto-detects
> `pdfusion-env` under `~/anaconda3/envs/` or `~/miniconda3/envs/`. Neither name
> is required — set `PDFUSION_PYTHON` to your env's `python.exe` path if you
> used something else.

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
> `requirements.txt` flatly installs the RAG + advanced extras (chromadb, the
> onnxruntime embedding stack, camelot, pdfplumber); `pyproject.toml` puts those
> behind `[project.optional-dependencies]` named `rag`, `advanced`, `all`. For
> the desktop app to fully work (RAG chat especially), install everything via
> `requirements.txt` or `pip install -e ".[all]"`.

> Two dependency notes worth not re-deriving. **Both OpenCV wheels are
> installed and neither is ours to choose**: `opencv-python-headless` is a
> direct BabelDOC requirement and `opencv-python` comes from
> `rapidocr-onnxruntime`, itself a hard BabelDOC requirement. They install to
> the same `cv2` package and PyInstaller collects it once, so there is nothing
> a pin can fix. And **torch is still installed in a dev/CI env** even though
> the bundle excludes it — argostranslate hard-requires `stanza==1.10.1`, which
> requires torch. See "Argos does not need torch" below.

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

The port comes from a socket the sidecar **binds, listens on, and keeps**
(`server.py:_bind_socket`), handed straight to
`uvicorn.Server(...).run(sockets=[sock])`. Picking a port by binding, reading
`getsockname()`, closing, and letting uvicorn re-bind left a window where
something else could take it. It `listen()`s there rather than leaving that to
uvicorn because READY is printed *before* `create_app()` and the lifespan run —
a client connecting in that gap should queue in the backlog, not take a
connection refused it has to know to retry.

Dropping to `uvicorn.Server(...).run()` also drops the tail `uvicorn.run()` has:
`if not server.started: sys.exit(STARTUP_FAILURE)`. `main()` repeats it. Without
it a lifespan that raises returns normally and the process exits **0** — and
since READY has already been printed, the shell would only notice by waiting out
its whole `HEALTH_TIMEOUT` and blaming `/auth/ping`.

### Import cost is a startup budget

`sidecar.rs` gives the READY line **90 s** (`READY_TIMEOUT`) and then
`/auth/ping` **30 s** (`HEALTH_TIMEOUT`). Those are sized for the PyInstaller
one-dir bootloader paging thousands of files past Defender on a first launch —
*not* for Python work. The Python side of startup is ~0.7 s and has to stay
there.

It got there by one rule, applied everywhere:

> **A package `__init__.py` re-exports only names that are free to import.**

That rule is load-bearing rather than stylistic, because importing a submodule
runs its parent's `__init__`. `processors/pdf_cache.py` is pure `sqlite3` +
`hashlib`, yet used to cost 4.9 s — `processors/__init__.py` did
`from .processor import PDFProcessor`, which is BabelDOC. Same shape in
`translators/__init__.py` (3.2 s of provider SDKs via `factory`) and
`api/__init__.py` (`from .server import create_app`, i.e. everything). So
`api/` and `rag/` re-export nothing at all; `processors/` keeps `events` and
`exceptions`; `translators/` keeps `base` and `translation_cache`.

The rest follows from it. BabelDOC and the RAG stack are imported inside the
handlers that use them (`routes/translation.py:_run_translation`,
`routes/rag.py:_build_chain`), with `from __future__ import annotations` +
`TYPE_CHECKING` so the type annotations still name them. `create_app()` is
called *after* READY is printed. Importing `api.server` went from **16.3 s to
0.7 s**, and RAG — off by default — no longer costs most users torch at all.

Two consequences worth keeping in mind:

- **The first Translate click would now pay BabelDOC's import.** Mostly it
  doesn't, because `_lifespan` starts an `engine-warm` daemon thread that
  imports `processors.processor` after READY, next to the existing
  `argos-prewarm` thread. Startup no longer *waits* on that import; it still
  *does* it. Budget **5-25 s**, not the 5 s a warm machine suggests — the two
  threads now genuinely contend for the GIL, where before the moved imports the
  prewarm thread found everything already resolved. That thread also loads the
  DocLayout model into `doc_layout_cache` (another ~3 s), but only when the
  asset is already on disk — see "Loading the layout model once" below.
- **A click can still beat that thread, so every relocated import runs off the
  event loop.** These are *blocking* imports inside `async def` handlers: on the
  loop they freeze the whole sidecar — health checks, `/pdf/file` for pdf.js,
  every other job's SSE stream — for their full duration. Measured before the
  fix: a Translate click during the warm window took **18.9 s** to return its
  `job_id`, and `/health` (33 ms idle) answered once in the next 40 s. So
  `routes/translation.py:_load_engine` and `routes/rag.py:_load_document_processor`
  exist only to be called through `await asyncio.to_thread(...)`, the same way
  `_build_chain` already was. Each stays inside the `try` that reports through
  `job.finish("error", ...)`: with the import on the job path, an `ImportError`
  that once stopped the sidecar from starting is now a per-job failure, and
  without a terminal SSE event the overlay waits forever.
- **PyInstaller is fine with this.** Its modulegraph walks bytecode including
  function bodies, so a function-level `from x.y import Z` is still statically
  visible and `pdfusion-sidecar.spec` needs no new `hiddenimports`. Only truly
  dynamic imports (`importlib.import_module(<variable>)`) need an entry there.

`tests/test_sidecar_boot.py` fails if any of this regresses.

### First-run engine setup

A translation needs ~290 MB that no wheel carries: BabelDOC's DocLayout ONNX
model, a table-detection model, 34 embedding fonts, 146 cmaps and a tiktoken
encoding (cached in `~/.cache/babeldoc`), plus the Argos en→vi pack. Before
this existed they were fetched implicitly, from inside `TranslationConfig`
construction on the first chunk of the first job, behind an overlay that said
"Initializing translator" — and offline the user was told
**`BabelDOC processing error in chunk 1: 1`**.

That string is the shape of the whole problem. **Every failure in BabelDOC's
asset layer is `exit(1)`** — no message, no type. `run_one_chunk` catches
`BaseException`, and the multiplexer interpolated it: `str(SystemExit(1))` is
`"1"`. `processors/exceptions.py:babeldoc_chunk_error` is where that is now
translated into a sentence, and it deliberately drops `original_error` —
`BabelDOCError.__str__` appends it and the job layer sends `str(exc)`, so
carrying the `SystemExit` through would staple the `1` back on the end.

`engine_assets.py` is the single source of truth for "installed", consulted by
the setup flow, the `POST /translate` pre-flight and that error message, so the
three cannot disagree. Two things about it:

- **It asks less of a job than of setup.** `engine_status()` counts the whole
  183-entry manifest, because that is what an offline-first install means.
  `engine_ready()` — the pre-flight — requires only the layout model
  (`_CORE_ASSET`). Fonts and cmaps are fetched per document and per language, so
  every install predating this flow has a partial cache that has been working
  fine; requiring the full set before a job may start would refuse work those
  machines have been doing for months.
- **It counts by `stat`, never by hash.** BabelDOC's own `verify_file` hashes
  every file, and the set is ~210 MB. This runs on every `POST /translate`.
  Integrity stays BabelDOC's job — it re-verifies and re-downloads what it
  doesn't like. A zero-byte file (an interrupted download) does not count.

`api/routes/setup.py` installs them, and is **the one long job in the sidecar
that is polled rather than streamed.** Every other long job gets a `job_id`
that survives a dropped SSE connection and can be reattached to (see
"Long-running jobs (SSE pattern)") — installing the engine can't use that
model at all, because it isn't a job with an id: it's a process-wide
singleton, one cache directory being written by at most one download,
regardless of how many browser tabs or reloads ask about it. A webview
reload has nothing to reattach *to* by `job_id`, so the next Install click
would start a second download into the same cache directory unless
something reattaches for it. `GET /setup/status` does that for free. Two
more rules it keeps:

- **Never call BabelDOC's sync wrappers** (`warmup()`,
  `restore_offline_assets_package()`). They run the coroutine through
  `assets.py:run_in_another_thread`, where `threading.excepthook` swallows
  `SystemExit` and the call returns `None` — the failure resurfaces much later
  as a `TypeError` unpacking that `None`. Only the `_async` variants, under an
  `asyncio.run` of our own, inside a `try` that names `SystemExit`.
- **Progress is observed, not reported.** Neither BabelDOC's downloaders nor
  `argostranslate`'s expose a hook, so the percentage is how much of the
  manifest has appeared on disk, and the phase is a noun the install thread
  writes as it moves (`_Phase.noun`, plain assignment — one writer, one reader).

Nothing in `engine_assets.py` or `routes/setup.py` imports babeldoc or
argostranslate at module level; both are on the boot path, and
`tests/test_sidecar_boot.py` covers both.

On the frontend, `App.tsx`'s `EngineGate` sits between the sidecar gate and the
workspace. It is also where a mid-session 409 lands: `useTranslation` sets
`engineSetupRequired` in the store, which overrides a previous "Not now" — the
user has just asked for the one thing the assets are for. The skip marker is a
`pdfusion.*` localStorage key behind pure helpers in `lib/engine-setup.ts`, the
same convention as `sidecar-recovery.ts`. Reading a PDF never waits on any of
this; only translating does.

Consequence for the boot threads: **pre-warm may materialize an asset, never
download one.** `server.py:_should_prewarm_argos` and
`routes/translation.py:_warm_translator` are gated on `argos_pack_ready()`;
`server.py:_warm_translation_engine` is gated on `babeldoc_core_ready()` before
it touches the layout model. They fire at boot and on document open, with
nothing on screen that could report a download or its failure — installing is
the setup flow's job, because that is the surface the user can watch.

The layout model is the newer half of that rule and the easier one to get
wrong, because the download hides one call down: `DocLayoutModel.load_available()`
→ `assets.get_doclayout_onnx_model_path()`, which fetches whenever `verify_file`
fails. Ungated it doesn't merely download unattended — `babeldoc.assets.download_file`
writes **in place**, with no temp + rename, and unlinks the file when the hash
doesn't match, so a boot-thread download racing the setup flow's download of
the same path can destroy the copy the user is watching a progress bar for.
`tests/test_engine_warm_gate.py` covers both directions.

### Sidecar supervision

READY isn't the end of the sidecar's lifecycle — two things watch it after
that, both in `sidecar.rs`:

- A background task polls the child every `SUPERVISOR_POLL_INTERVAL` (250 ms)
  via `try_wait()`. An exit it didn't expect emits `sidecar://exited { code }`.
  What makes it "didn't expect": `SidecarHandle` carries a `shutting_down`
  `AtomicBool`, and `shutdown()` sets it **before** taking the child out of the
  mutex — that ordering is what stops an intentional kill (`restart_app`,
  `RunEvent::ExitRequested`) from being reported as a crash. Reversing it would
  open a window where the poller observes "child gone" without yet observing
  "on purpose."
- The child is confined to a Windows Job Object with
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (`confine_to_job_object`), so it dies
  with this process no matter how this process dies — crash, Task Manager,
  anything short of the OS itself going down. The job handle is **never
  closed**: `KILL_ON_JOB_CLOSE` fires when the *last* handle to the job
  closes, so leaking it for the process's lifetime is what ties the child's
  life to ours. Both the poll and the job assignment are best-effort — neither
  can fail sidecar startup.

On the frontend, `useSidecar.ts` reacts to `sidecar://exited` by calling the
existing `restart_app` command — a full relaunch, not an in-place respawn, for
the same `OnceCell` reason `restart_app` itself documents. Whether it actually
does that is gated by `lib/sidecar-recovery.ts`'s `shouldAutoRestart`: a 60 s
cooldown recorded under a `localStorage` key, not in-memory state, because
`app.restart()` tears down the whole JS context a module or React variable
would otherwise live in. That timestamp is **never cleared by a successful
`ready()`** — only elapsed time clears it. Clearing it on ready was the bug in
the first version of this: a sidecar that crashes on every boot would
relaunch, reach ready, wipe its own cooldown marker, and crash-loop forever
with no way to ever reach the manual "stopped unexpectedly" screen. Leaving it
to expire on its own still gives an unrelated crash days later a fresh
attempt. A separate, one-shot `SIDECAR_RESTART_TOAST_KEY` — set right before
the restart, consumed and cleared the next time `ready()` runs — is what tells
the user their session just got reset, without reusing (and thereby
corrupting) the cooldown key for that purpose.

### Module layout

| Path | Responsibility |
|---|---|
| `desktop/src-tauri/src/main.rs` | Tauri entry; defers to `desktop_lib::run()` |
| `desktop/src-tauri/src/lib.rs` | Builder + plugins + sidecar spawn on setup + shutdown hook |
| `desktop/src-tauri/src/sidecar.rs` | Python locate, child process, READY parsing, health-poll |
| `desktop/src-tauri/windows/installer-hooks.nsh` | NSIS hooks: register PDFusion under `.pdf` "Open with" (and never as the default) |
| `desktop/src/App.tsx` | Shell: ThemeProvider → QueryClientProvider → Workspace |
| `desktop/src/components/layout/` | `Header`, `ContextBar`, `MainLayout` (resizable splits + the workspace's keyboard shortcuts), `DropOverlay` |
| `desktop/src/components/pdf-viewer/` | `PdfViewer` (layout, scroll, zoom), `page-renderer.ts` (canvas recycling, text layers), `text-selection.ts`, `find-highlight.ts`, `FindBar`, `ViewerToolbar`, `pdf-viewer.css` — see "PDF viewer" |
| `desktop/src/lib/pdf-viewer/` | Pure: page geometry (`layout.ts`), find matching (`find.ts`), which pages a new rolling PDF changed (`artifact-swap.ts`), key → shortcut (`shortcuts.ts`) |
| `desktop/src/components/chat/` | `ChatPanel`, `UserMessage`, `AssistantMessage`, `ActionLog`, `ReferenceList`, `ChatInput` |
| `desktop/src/components/settings/` | `SettingsSheet` (tabs per service) |
| `desktop/src/components/translation/` | `ProgressOverlay`, `TranslatedFileActions` (Save / Open / Show in folder) |
| `desktop/src/components/ui/` | shadcn-generated primitives (button, dialog, sheet, …) |
| `desktop/src/lib/api-client.ts` | Typed HTTP wrapper with bearer-token + sidecar URL helpers |
| `desktop/src/lib/sse.ts` | Authenticated SSE reader (native EventSource can't set headers) |
| `desktop/src/lib/store.ts` | Zustand store for UI state |
| `desktop/src/lib/export-pdf.ts` | Pure save-flow logic + path helpers (deps injected, so it's unit-testable) |
| `desktop/src/lib/openapi.json` | Checked-in OpenAPI schema; regenerated by `export_openapi.py`, CI fails if stale |
| `desktop/src/lib/api-types.d.ts` | Checked-in generated TS types (`openapi-typescript`); a `pnpm build` prestep, CI fails if stale |
| `desktop/src/hooks/` | `useSidecar`, `useConfig`, `useTranslation`, `useRagIndex`, `useRagAsk`, `useExportTranslated` |
| `desktop/src/components/setup/` | `SetupScreen` (first-run engine install) |
| `desktop/src/lib/engine-setup.ts` | Pure: the skip marker and whether the setup screen is due |
| `src/desktop_pdf_translator/engine_assets.py` | What "the offline engine is installed" means; no heavy imports |
| `src/desktop_pdf_translator/api/server.py` | FastAPI app + uvicorn entry + port discovery |
| `src/desktop_pdf_translator/api/auth.py` | Bearer-token middleware |
| `src/desktop_pdf_translator/api/jobs.py` | In-memory job registry + asyncio.Queue per job for SSE |
| `src/desktop_pdf_translator/api/routes/*` | `config`, `translation`, `rag`, `pdf` route modules |
| `src/desktop_pdf_translator/api/schemas.py` | Pydantic request/response models, wired to every route via `response_model=` |
| `src/desktop_pdf_translator/api/sse_schemas.py` | Pydantic mirrors of the SSE event payloads — documentation-only, no route validates against them |
| `src/desktop_pdf_translator/api/export_openapi.py` | `python -m …export_openapi` — the sidecar's OpenAPI schema (SSE payloads stitched in), written to `desktop/src/lib/openapi.json` |
| `src/desktop_pdf_translator/config/` | `ConfigManager` + Pydantic `AppSettings` (unchanged) |
| `src/desktop_pdf_translator/processors/` | `PDFProcessor` async generator wrapping BabelDOC (unchanged) |
| `src/desktop_pdf_translator/translators/` | `BaseTranslator`, OpenAI/Gemini/Anthropic/Argos + `TranslatorFactory` |
| `src/desktop_pdf_translator/translators/rate_limiter.py` | Process-wide token-bucket QPS limiter, one singleton per LLM service |
| `src/desktop_pdf_translator/rag/` | ChromaDB + `EnhancedRAGChain` (deep-search/web-research was dropped in `35bca2c`) |
| `src/desktop_pdf_translator/rag/onnx_embeddings.py` | MiniLM embeddings on onnxruntime — what replaced sentence-transformers |
| `src/desktop_pdf_translator/translators/_sbd_compat.py` | The `stanza` stub that lets the bundle drop torch |
| `src/desktop_pdf_translator/utils/` | API key encryption; `file_export.py` (durable copy of a translated PDF); `logging_setup.py` (shared rotating `app.log` config); `paths.py` (`appdata_dir()`/`logs_dir()`) |
| `src/desktop_pdf_translator/translators/translation_cache.py` | Persistent **paragraph-level** SQLite cache (singleton `get_translation_cache()`) |
| `src/desktop_pdf_translator/processors/pdf_cache.py` | Persistent **whole-PDF** SQLite cache (singleton `get_pdf_cache()`) |
| `src/desktop_pdf_translator/processors/doc_layout_cache.py` | Process-wide DocLayout-YOLO model, loaded once (`get_shared_doc_layout_model()`) |

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
| GET | `/setup/status` | Which engine assets are installed, plus the running install's phase and the last one's error. Stat calls only — polled twice a second during an install |
| POST | `/setup/engine` | Start installing the engine assets, or report the one already running; answers with the same body as `/setup/status` |
| POST | `/translate` | Start translation job → returns `{ job_id }`. `source_lang` / `target_lang` / `service` are `None`-defaulted (config applies); an unsupported pair is refused with **422** and a missing engine with **409**, both before the job is created. `bypass_cache: bool` forces a full re-translate (used by the "Re-translate" button). There is deliberately **no `output_dir`** — output always lands in a per-job `%TEMP%` dir that the cleanup paths know about |
| GET | `/translate/{job_id}/events` | SSE: `progress`, `chunk_ready`, `paragraph_translated`, `done`, `error`, `cancelled`. **`chunk_ready` arrives in priority order, not page order** — nearest the viewer's page first — so `chunk_index` is not a completion count and `pages_in_chunk[1]` is not a running total. Accumulate with `lib/translation-progress.ts`; page totals come from `total_pages` (`total_chunks` is not a page count — Argos runs 3-page chunks) |
| POST | `/translate/{job_id}/cancel` | Cancel an in-flight translation |
| POST | `/rag/index` | Index a PDF into ChromaDB → returns `{ job_id }`. A document's id is the **SHA-256 of its bytes**, derived by the sidecar — the request carries no `document_id`, and `done` returns it. It used to be the file name stem, so two different `paper.pdf`s shared one index (#59) |
| GET | `/rag/index/{job_id}/events` | SSE: `progress`, `done`, `error` |
| POST | `/rag/ask` | Ask the RAG chain → returns `{ job_id }`. `document_id` is **required** (422 without it): a question is about exactly one document, and there is no "search every document" mode. A document with no chunks ends in an `error` event, not an empty answer |
| GET | `/rag/ask/{job_id}/events` | SSE: `progress`, `answer`, `done`, `error`. Retrieved chunks ride on `answer.pdf_references` (**not** `pdf_sources`); their `page` is **1-indexed**, or `null` when the chunk has none. Chunk metadata is 0-indexed and `PdfViewer.scrollToPage` counts from 1, so `rag_chain._display_page` converts at that one boundary |
| DELETE | `/rag/document/{document_id}` | Remove an indexed document from the vector store |
| GET | `/pdf/file?path=...` | Stream a PDF from disk (used by pdf.js client-side) |
| POST | `/pdf/export` | Copy a translated PDF to a user-chosen permanent path (`{source_path, destination_path, protect_path?}` → `{saved_path, bytes_written}`). `protect_path` is the opened document; it's refused as a destination |

### Generated API types (OpenAPI)

The HTTP/SSE boundary between the sidecar and the desktop app is described by
two generated, checked-in artifacts rather than hand-copied interfaces —
three of the latter had already drifted from `api/schemas.py` in production
before this existed (language fields silently never sent, `pdf_references`
vs. `pdf_sources` — issue #13, a dead `deep_search`/web-research pair of
fields — issue #14). See issue #27.

1. `python -m desktop_pdf_translator.api.export_openapi --output desktop/src/lib/openapi.json`
   calls `create_app().openapi()` — the same schema FastAPI serves at
   `/openapi.json` — and additionally stitches the SSE routes' real payload
   shapes into it. FastAPI has no idea what `EventSourceResponse` sends
   (every SSE route is a bare `-> EventSourceResponse`, no `response_model`),
   so without this step those three routes' schemas are an empty
   placeholder. The payload shapes live in `api/sse_schemas.py` — mirrors of
   `processors/events.py`'s dataclasses and the ad-hoc dicts in
   `api/jobs.py` / `api/routes/rag.py` / `api/routes/translation.py`. They
   are *not* real `response_model=`s — SSE payloads are never validated at
   runtime (see "Long-running jobs" below) — and the stitching only happens
   in this export script, never in `server.py`, so it carries zero runtime
   risk to the real routes. `tests/test_sse_schemas.py` guards against these
   mirrors drifting from `processors/events.py`.
2. `pnpm run generate:api-types` (in `desktop/`) runs `openapi-typescript`
   over that file to produce `desktop/src/lib/api-types.d.ts`. It's a
   `pnpm build` prestep, so a build always regenerates it from whatever
   `openapi.json` is on disk.

Regenerate by hand after touching `api/schemas.py`, `api/sse_schemas.py`, or
any route:
```bash
python -m desktop_pdf_translator.api.export_openapi --output desktop/src/lib/openapi.json
cd desktop && pnpm run generate:api-types
```

CI enforces both files stay current, split by toolchain since neither
existing job has both Python and Node:
- **`python` job**: regenerates `openapi.json`, then `git diff --exit-code`s it.
- **`desktop` job**: `pnpm run check:api-types` (`openapi-typescript … --check`)
  compares a fresh generation against the checked-in `api-types.d.ts` without
  writing to it. This step runs *before* `pnpm build` — `pnpm build` would
  otherwise regenerate and overwrite the file first, making the check
  trivially pass against its own fresh output.

Frontend code imports these generated types instead of hand-declaring
interfaces that mirror the wire format
(`import type { components } from "@/lib/api-types"`). Two loose ends worth
knowing about:
- `ConfigResponse`'s `translation`/`rag`/`gui`/`processing` fields had to
  become real nested models (`TranslationSettings` etc., `config/models.py`)
  instead of `Dict[str, Any]` — left loose, the generated type would have
  erased exactly the fields this issue was about.
- `streamJobEvents<T>`'s `data as X` casts in the hooks don't disappear —
  `SseEvent.type` is a bare `string`, not a discriminant on `T`, so narrowing
  an SSE union still needs an assertion. What changed is that `X` is
  generated, not hand-maintained.

### Long-running jobs (SSE pattern)

Long-running endpoints (translate, index, ask) follow the same pattern:
1. `POST /resource` returns `{ job_id }` immediately.
2. The actual work runs in a background asyncio task that appends events to
   the job's `history` (`api/jobs.py`) and wakes anyone waiting on it.
3. `GET /resource/{job_id}/events` opens an SSE stream that replays whatever
   in `history` is newer than the `last_seq` query param (default 0), then
   tails live events the same way.
4. A terminal event (`done`, `error`, or `cancelled`) closes the stream.

This replaces the previous `QThread + new asyncio loop` pattern from the PySide6 GUI.

**A job survives a dropped SSE connection** — a webview reload, a network
blip — instead of being discarded the moment its consumer detaches (issue
#28). State lives entirely in the shared `Job`, not a per-consumer queue, so
a reattaching `GET .../events?last_seq=N` picks up exactly what it missed
and `/cancel` keeps working on a job nobody is currently streaming. Only
`_sweep_stale_locked` ever frees a job — once it has had no live worker and
no fresh terminal event for `_JOB_TTL_SECONDS` (an hour). A finished job's
small event history is left to linger for that whole window by design (a
reattach might still be coming); `finish()` drops the heavy `processor`
handle immediately so that cost stays small regardless. On the frontend,
`lib/sse.ts`'s `streamJobEvents` is what actually reconnects —
`useTranslation`/`useRagIndex`/`useRagAsk` all go through it instead of the
lower-level `streamEvents`, so a transient drop within the same page load is
invisible rather than surfacing as "stream ended unexpectedly". That only
covers *that* window: nothing persists the active job id across a full page
reload (there's no stored handle to reattach with afterward), and there's no
job-history table or "Recent" list yet — both remain open, see issue #28.

**`/setup/engine` is still the one exception**, but no longer for that
reason: it isn't that the registry mishandles a dropped connection (it
doesn't, anymore) — it's that the install has no `job_id` at all to reattach
*to*, being a process-wide singleton rather than a per-request job. See
"First-run engine setup".

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
   drain runs `cleanup_partial_artifacts()`, which at its default `keep=1`
   unlinks all but the newest rolling PDF (mid-run the pipeline passes
   `keep=2` — see "Loading the layout model once"). So `TranslationState.status` has a `cancelling` state covering
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

### Loading the layout model once, and what that does *not* fix

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

**What this does not fix, and the reason `_PAGES_PER_CHUNK_ARGOS` is a live
question.** The layout model was never the largest per-chunk fixed cost.
`FontMapper.__init__` sha3_256-verifies and loads **all 34 embedding fonts
(~250 MB)** for `lang_out`, and nothing memoizes it — not `fontmap.py`, not
`assets.py`. BabelDOC constructs one per stage: `ILCreater`, `ParagraphFinder`,
`StylesAndFormulas`, `ILTranslator`, `Typesetting`, `PDFCreater`. Measured
against the installed babeldoc on a warm cache: **1.02 s per `FontMapper`, ≈6 s
per chunk**, none of which this cache touches. So "the reload is fixed" is not
on its own a reason to shrink a chunk — shrinking one multiplies ~6 s by the
extra chunks it creates, and on the Argos path (`_MAX_PARALLEL_CHUNKS_ARGOS = 2`)
that can outweigh what the cache saves. Measure an end-to-end Argos run before
touching `_PAGES_PER_CHUNK_ARGOS`; the constant's real justification is
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

### Argos does not need torch

The offline translator used to drag in 590 MB of tensor library, and the reason
is one line: `argostranslate/sbd.py` does an unguarded top-level `import stanza`,
`argostranslate/translate.py` imports from `sbd`, and `import stanza` pulls both
torch and transformers. Worse, the upstream en→vi pack ships
`stanza/en/tokenize/ewt.pt`, a torch checkpoint — so `StanzaSentencizer` was
genuinely selected and torch genuinely used, not merely imported.

Three changes remove it, and all three are needed:

- **The pack carries MiniSBD instead.** `fetch-offline-assets.ps1` repacks the
  staged `.argosmodel`: `stanza/` out (0.75 MB), `minisbd/en.onnx` in (0.19 MB,
  onnxruntime). The repack is idempotent, so it also fixes a pack staged before
  this existed, and it writes through a sibling temp file + `os.replace`.
- **The splitter is pinned, not merely preferred.**
  `argos_translator._configure_argos_settings` sets
  `settings.chunk_type = ChunkType.MINISBD`. Assignment rather than the
  `ARGOS_CHUNK_TYPE` env var, which `argostranslate/settings.py` reads at
  *module import* time — `PackageTranslation.__init__` reads `chunk_type` per
  translation, so the assignment holds whatever the import order, and whatever
  sbd model a runtime-downloaded pack happens to carry.
- **The import is satisfied by an empty module.**
  `translators/_sbd_compat.install_stanza_stub()` registers a bare
  `ModuleType("stanza")` when the real one is absent. `stanza.Pipeline` is only
  touched inside `StanzaSentencizer.lazy_pipeline`, which is now never
  constructed, so nothing more is needed. It is a no-op when stanza *is*
  installed — which it always is in dev, because argostranslate hard-requires
  it — so dev and the bundle differ only in whether the import is real.

`pdfusion-sidecar.spec` then excludes `stanza`, `torch`, `transformers` and
`sentence_transformers`. Measured like-for-like on the same commit and the same
staged assets, that is **1352 MB → 857 MB unpacked (-495 MB)** and a
**98.7 MB → 43.8 MB** sidecar exe. Two consequences:
**`tests/test_sidecar_smoke.py`'s frozen half is the only check that can catch
an over-exclusion**, and `FORBIDDEN_AT_BOOT` in `test_sidecar_boot.py` still
lists torch and stanza — the dev env has them (argostranslate hard-requires
stanza, which requires torch), the bundle does not. `transformers` and
`sentence_transformers` are *not* on that list: nothing requires them any more,
so a clean install doesn't have them to import, and the list's companion test
requires every name on it to be installed.

RAG embeddings had to move off torch in the same change or the excludes would
have broken Chat: `rag/onnx_embeddings.py` runs the *same*
`paraphrase-multilingual-MiniLM-L12-v2` weights (multilingual, 384-dim, chosen
for Vietnamese) through onnxruntime + tokenizers, reimplementing the only two
things sentence-transformers did here — tokenize, then mean-pool over the
attention mask, per the model's `modules.json`. The feed is built from
`session.get_inputs()` rather than hardcoded, so a re-export with a different
signature still works. The ~470 MB first-use download is unchanged.

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

1. **Chunking unit = paragraph**, not page. BabelDOC's `ParagraphFinder` groups characters into `PdfParagraph` objects (one body paragraph, heading, caption, list item, etc.), then `ILTranslator.translate_paragraph` issues **one `translate()` call per paragraph** in a thread pool. A typical 10-page paper → dozens to hundreds of small calls, parallelized across up to `max_parallel_chunks` BabelDOC sub-jobs × `pool_max_workers` threads each — 32-64 at the defaults. `_create_babeldoc_config`'s `qps=4` argument doesn't gate any of that: BabelDOC only applies it through a rate limiter wired up from its own CLI entry point (`babeldoc/main.py`), which this project never calls. The real, process-wide throttle across every sub-job and every concurrent job is `translators/rate_limiter.py`, applied inside `translate()` itself (see point 4 below).

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

4. **Every LLM backend's SDK call goes through `BaseTranslator._call_with_backoff(request)`**, never a bare `self.client...create(...)`. It acquires a token from `rate_limiter.get_rate_limiter(self._SERVICE_NAME)` — a class attribute each backend sets (`"openai"`/`"gemini"`/`"anthropic"`) naming a process-wide limiter singleton shared across every job, sub-job and worker thread — then retries on 429/5xx/408/409 *and* on transport failures, with jittered exponential backoff (`_MAX_RETRIES`, fatal 401/403 excluded). That classifier exists because the SDK clients are constructed with `max_retries=0`: the OpenAI/Anthropic SDKs default to retrying twice on their own — including on connection and timeout errors — and those inner attempts don't take a token, so leaving the default in place lets a 429 storm blow well past the configured QPS. Disabling it without widening the classifier would have just moved those errors from "retried by the SDK" to "an immediately lost paragraph" — a real regression caught in review of the original PR.

   Transport failures carry no status code, so `is_retryable_translation_error` matches them by class **name along the MRO** (`_RETRYABLE_ERROR_NAMES`) — never `isinstance`, because `base.py` has to import with none of the provider SDKs or httpx installed. Two names cover everything, which is the whole reason it walks the MRO instead of testing the concrete class: `APIConnectionError`, because openai and anthropic funnel every transport failure into it via their `except Exception` fallback and both SDKs' `APITimeoutError` subclasses it; and `TransportError`, because google-genai does *not* wrap, so raw httpx exceptions arrive here and all of them derive from it. Listing leaf names instead (`ReadTimeout`, `ConnectError`, …) is the version that shipped first, and it silently missed `ReadError`, `WriteError`, `NetworkError`, `ProxyError` and `CloseError` — on Gemini, a socket-level read failure stayed an immediately-lost paragraph.

   `max_qps` (an `Optional[float]` on each service's settings, `None` = the built-in per-service default in `_DEFAULT_QPS_BY_SERVICE`) is popped in `BaseTranslator.__init__` like the other cross-cutting kwargs and applied to the shared limiter **once, at construction** — not per-call, since a translator's `max_qps` never changes over its lifetime and re-applying it on every paragraph would take the limiter's lock to write back the same value. It *is* applied unconditionally, resolving `None` through `default_qps_for()`, because the limiter outlives the translator: skipping the call when no override is set would let a cleared one survive — raise the rate once, delete the line from `config.toml`, and the sidecar keeps running at the raised rate until it restarts. There's no Settings-sheet control or `PUT /config` field for it yet; the only way to change it today is `<service>.max_qps` in `config.toml` (see the commented examples in `default_config.toml`), most relevant for Anthropic's default of 1.0 QPS (sized for its ~50 RPM entry tier — a higher tier gets nowhere near its real limit without overriding this).

   A new backend must do the same three things every existing one does: check `self.is_cancelled()` at the very top of `translate()` (before the cache lookup — a cancelled job shouldn't even pay for a cache read); add `except TranslationCancelled: return text` **before** `except Exception` — reversed, a cancelled paragraph gets funnelled into `_handle_translation_error` and counted as a failure it wasn't; and set `_SERVICE_NAME` if it wants rate limiting at all — `None` genuinely opts out, handled by an explicit branch in `_call_with_backoff` rather than passed through, since `get_rate_limiter(None)` would otherwise build a real `None`-keyed bucket at the fallback rate and share it between every backend that never named a service. (That branch re-checks the cancel flag itself, so opting out of the limiter doesn't also opt out of the cancel check that rode on its `acquire()`.) `cancel_event` is threaded in by `PDFProcessor` (one `threading.Event` per job), so a Cancel click stops new LLM calls within one in-flight paragraph per worker thread — the asyncio-level `task.cancel()` alone never reached code already running synchronously on a BabelDOC worker thread. Argos gets the same top-of-`translate()` cancel check but no rate limiter or backoff (`_SERVICE_NAME` stays `None`) — it's local, and the existing "a late `event.set()` on an abandoned batch entry is harmless" behavior already covers it.

   The one deliberate exception to "[Translator failures are counted, not swallowed](#translator-failures-are-counted-not-swallowed)": a cancelled `translate()` call returns source text **without** touching `failed_translations` — it's an intentional stop, not a failure, and counting it would make a cancelled run look like a partial one if anything ever inspected the counters after cancellation. Total retry attempts (not distinct paragraphs) land in `retry_count`, which `CompletionEvent` and the `/translate` SSE `done` payload also carry as `retry_count` — issue #22 asked to "surface" it; nothing in the UI reads it today, so consider that half-done, not wired to a banner.

### PDF viewer

Both panes are `components/pdf-viewer/PdfViewer.tsx` (#30). Its pure half lives
in `lib/pdf-viewer/` and is unit-tested. The rest is imperative pdf.js work kept
out of React state. There are five invariants, and each one is easy to break by
"simplifying":

- **A slot's size comes from the layout model, never from a canvas.** Only the
  visible pages ±`RENDER_RADIUS` (3) hold a canvas (`page-renderer.ts`). Every
  other page is released: its render cancelled, its backing store zeroed
  (`canvas.width = 0`, not left to GC), its text layer removed, `page.cleanup()`
  called. So the column's height, go-to, zoom anchoring and "which page am I
  on" are all computed from `layout.ts:pageGeometry`, and the JSX sizes each
  slot through the *same* `slotSize`. Round differently in one place and a
  go-to lands on the wrong page a few hundred pages in. Slots use `ring-1`
  rather than `border` for the same reason: a border would inset the canvas.
  Page sizes start as page 1's and are corrected in the background; Chromium's
  scroll anchoring keeps the reader in place meanwhile.
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
  the worker. Every async render re-checks a document *generation*, so a render
  that lost a race is dropped rather than drawn. A failed swap keeps the
  previous version on screen. None of this removes the need for the backend's
  `keep=2` (see "Rolling-PDF pruning keeps two files"): the old version is still
  what's displayed, and still what pdf.js issues range requests against, until
  the new one has loaded.
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

Synchronized scrolling between the panes (the first item in #30) is
deliberately not implemented: the panes scroll and zoom independently.

### React state ownership

- **TanStack Query** owns all server state (`useConfig`, `useOptions`).
- **Zustand store** (`lib/store.ts`) owns ephemeral UI state: current PDF paths, the translated-artifact change log, active job ID, RAG enabled flag, chat drawer open/closed.
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

- **Plugins enabled**: `opener` (open external URLs), `dialog` (file picker), `single-instance` and `window-state` — that's all. `shell` and `fs` were registered but never imported by `desktop/src`; PDFs reach the viewer over HTTP from the sidecar, and Save/Open/Reveal go through the app commands in `lib.rs`. Don't re-add a plugin "just in case": every one widens what an injected script can invoke. Same reasoning inside a plugin: the capability grants `opener:allow-open-url` + `opener:allow-default-urls` rather than `opener:default`, because that set also carries `allow-reveal-item-in-dir` — a second, unvalidated route to the reveal that `reveal_path_in_file_manager` exists to gate. The app commands call the plugin's **Rust** API (`app.opener()`), which capabilities don't apply to, so narrowing the webview's grant costs nothing. The two new plugins cost nothing there either: `single-instance` has no JS API at all, and `window-state`'s (`saveWindowState` / `restoreState`) is left ungranted — save and restore happen in Rust on window create and on exit, so the webview never needs to ask.
- **Single instance**: registered **first**, before every other plugin — a second launch has to be turned away before the rest of the app builds, or you get two windows, two sidecars, and two writers on one `chroma_db` + SQLite WAL set. Its callback focuses the existing window and, if the second launch named a PDF, emits `pdfusion://open-file` so that document opens in the running app. The *first* launch's own argv is read by the `initial_file_argument` command; `App.tsx` handles both through the same `openDocument`.
- **Window**: 1400×900 default, min 1024×700 — then `tauri-plugin-window-state` restores whatever the user last left. `GUISettings.window_width/height` were deleted with it: they predate the Tauri migration and nothing ever read them. `withGlobalTauri` is off — `__TAURI_INTERNALS__` (which `lib/tauri-ready.ts` waits on) is injected regardless; the flag only adds the legacy `window.__TAURI__` global.
- **Boot screen** (`components/StartupScreen.tsx`): app-level copy ("Starting PDFusion…"), a Retry and a "Show logs folder" button. Those two are offered in the `error` branch **and**, after `SLOW_START_MS` (15 s), in `starting` — otherwise a sidecar that never comes up leaves the user on a bare spinner for the full `READY_TIMEOUT` + `HEALTH_TIMEOUT`, which is two minutes. A healthy boot reaches READY in about a second, so anything still spinning at that mark is already abnormal. Retry calls `restart_app`, which relaunches the process rather than re-spawning the sidecar — the handle is a `OnceCell` set once per process, so re-entering that lifecycle would mean two spawn paths and a window with two Python processes. `restart_app` has to repeat the exit work by hand (`save_window_state`, `cleanup_translate_temp_dirs`): `AppHandle::restart` routes through `RunEvent::ExitRequested` **only when called off the main thread**, and a synchronous command handler runs on it, so it takes the `cleanup_before_exit` branch — resource tables cleared, windows hidden, nothing else. The `ExitRequested` arm in `lib.rs` and `window-state`'s own `RunEvent::Exit` hook both stay silent. Anything added to the exit path has to be added there too. The `PDFUSION_PYTHON` hint is behind `import.meta.env.DEV`; it describes this repo's dev setup and means nothing to someone who installed the `.msi`.
- **CSP**: set in `tauri.conf.json` — `default-src 'self'` with `connect-src` widened to `http://127.0.0.1:*` (the sidecar) plus Tauri's IPC origin, `worker-src blob:` (pdf.js), and `style-src 'unsafe-inline'` (Tailwind's runtime styles). Tauri nonces its own init script, so `script-src` stays at `'self'`. It applies to the bundled app only — in `pnpm tauri dev` the page is served by Vite, which Tauri doesn't inject headers into, so **a CSP break shows up first in `pnpm tauri build`**, not in dev.
- **CORS / dev origins**: the sidecar's allowlist (`server.py:_allowed_origins`) is exactly Tauri's custom-protocol origins, plus Vite's `localhost:1420` **only in dev**. Which one applies is a two-sided handshake: the shell sets `PDFUSION_DEV_ORIGINS` to `"1"`/`"0"` from `cfg!(debug_assertions)` (`sidecar.rs:dev_origins_flag`), on both spawn paths. The sidecar must **not** decide this from `sys.frozen` alone — frozen means "PyInstaller built it", not "shipped app", and discovery prefers a staged `binaries/*.exe` over local Python, so after `build-sidecar.ps1` a `pnpm tauri dev` run pairs a *frozen* sidecar with a *Vite-hosted* webview. Getting that wrong rejects every request the app makes (preflights → `400 Disallowed CORS origin`) and looks exactly like the sidecar failing to start. `sys.frozen` remains the fallback for a sidecar started without the shell. Don't swap `debug_assertions` for `tauri::is_dev()`: that's `!cfg!(feature = "custom-protocol")` and this crate declares no `[features]`, so it's `true` even in a release bundle.
- **Sidecar lifecycle** is wired in `lib.rs::run()`'s `setup` and the `RunEvent::ExitRequested` handler kills the child process.
- **Sidecar cwd & writable paths**: the child is spawned with cwd = `%LOCALAPPDATA%\PDFusion\` (`sidecar::appdata_dir`), **not** the install dir (`C:\Program Files\PDFusion\` is read-only for non-admins → `WinError 5` on any relative-path write). `lib.rs::setup` pre-creates the AppData subdir layout (`sidecar::ensure_appdata_layout`) before spawn so Python subsystems don't race on first-run `mkdir`.
- **Per-job translation output** is a throwaway `%TEMP%\pdfusion-translate-<rand>\` dir (not a persistent `translated_pdfs/`). It's wiped three ways: by the next job, by the Tauri `ExitRequested` handler (`sidecar::cleanup_translate_temp_dirs`), and by the FastAPI lifespan orphan sweep on sidecar startup (`server.py:_sweep_orphan_translate_dirs`, only dirs older than 1h). Persistent translated PDFs live in the whole-PDF cache instead.
- **Drag and drop**: files dropped on the window arrive through `getCurrentWebview().onDragDropEvent` (`hooks/useFileDrop.ts`) with real paths, and open through the same `openDocument` as the picker and the command line; `DropOverlay` shows what's being dragged. That works because `dragDropEnabled` is left at its default (true), and on Windows that **disables HTML5 drag and drop inside the webview**. Anything that needs HTML5 DnD would have to turn the shell's handling off, and this path with it. `enter` is the only event that carries paths. A drag with no files at all (text dragged out of a page) is ignored rather than refused.
- **"Open with PDFusion"** is registered by `windows/installer-hooks.nsh` (`bundle.windows.nsis.installerHooks`). It writes a `PDFusion.pdf` ProgID, a `.pdf\OpenWithProgids` value and `Applications\PDFusion.exe` under `SHCTX` (HKCU for this per-user install), and removes exactly those on uninstall. **Not `bundle.fileAssociations`**: Tauri's NSIS `APP_ASSOCIATE` overwrites the default value of `Software\Classes\.pdf`, which makes PDFusion the default PDF reader on any machine where the user never picked one. The chosen file arrives as argv, through `initial_file_argument` on a first launch and the single-instance handoff otherwise. Only `pnpm tauri build` exercises the hook (`release.yml` on a tag); CI's `desktop` job never does.
- **Sidecar discovery** order (see `desktop/src-tauri/src/sidecar.rs`):
  1. **Bundled exe** — `pdfusion-sidecar-<triple>.exe` resolved via `BaseDirectory::Resource`. This is what end users hit (shipped via `bundle.externalBin` in `tauri.conf.json`).
  2. **Dev fallback** — Python interpreter chain: `PDFUSION_PYTHON` env var → `~/anaconda3/envs/{pdfusion,pdfusion-env}/python.exe` → `~/miniconda3/envs/{pdfusion,pdfusion-env}/python.exe` → `python` on PATH, then `python -m desktop_pdf_translator.api.server` with `PYTHONPATH=<root>/src`.

## Building the desktop installer

```powershell
# 1. Stage the engine assets the installer ships (~290 MB into assets/).
#    Network + several minutes; skips whatever is already staged. Omit this
#    and the build still succeeds — it prints a WARN per missing asset and the
#    app downloads them on first run instead. This also repacks the Argos pack
#    off stanza and onto MiniSBD; see "Argos does not need torch".
conda activate pdfusion
./fetch-offline-assets.ps1

# 2. Build the standalone sidecar (PyInstaller, one-dir).
#    Output: dist/pdfusion-sidecar/{pdfusion-sidecar.exe, _internal/}
#    Then staged into desktop/src-tauri/binaries/.
pip install -e ".[dev]"          # ensures pyinstaller is available
./build-sidecar.ps1

# 3. Build the Tauri installer.
#    tauri.conf.json's beforeBundleCommand also re-runs build-sidecar.ps1 so
#    step 2 is technically optional, but doing it first lets you sanity-check
#    the bundled sidecar in isolation before the slow Tauri bundle step.
#    `fetch-offline-assets.ps1` is NOT wired into that hook: it needs the
#    network, and a bundle step that silently downloads a third of a gigabyte
#    is the problem this staging exists to fix.
cd desktop
pnpm tauri build
# → desktop/src-tauri/target/release/bundle/nsis/PDFusion_<version>_x64-setup.exe
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
bootloader requires to find `pythonXYZ.dll` (e.g. `python311.dll` for this project's Python 3.11) et al. First build is slow (~10-20 min).

**NSIS is the only bundle target, and it installs per user.**
`bundle.targets` is `["nsis"]` with `nsis.installMode: "currentUser"`, so the
app lands in `%LOCALAPPDATA%\Programs\PDFusion` with no UAC prompt. The
per-machine WiX `.msi` is gone: it was the origin of the read-only-cwd bug class
the code works around (`C:\Program Files\` is not writable for non-admins), and
pushing the thousands of `_internal/**/*` files through WiX was slow. Re-add
`"msi"` to `bundle.targets` if an IT-deploy story ever needs one. Note the
install dir is now writable — that does **not** make the AppData cwd work in
`lib.rs::setup` redundant, since a machine upgraded from an MSI install is
still out there.

On top of that, `fetch-offline-assets.ps1` stages two runtime asset sets that
the spec bundles when present (`_internal/argos_pack/`,
`_internal/babeldoc_assets/`) and warns about when absent:

| Staged path | What | Consumed by |
|---|---|---|
| `assets/argos/translate-en_vi.argosmodel` | Argos en→vi pack, ~80 MB | `argos_translator.py:_find_bundled_pack` |
| `assets/babeldoc/offline_assets_<tag>.zip` | BabelDOC layout models, fonts, cmaps, ~210 MB | `engine_assets.py:bundled_babeldoc_zip` |

Both are gitignored. `<tag>` is a hash of BabelDOC's own asset manifest, so the
zip is only valid for the babeldoc version it was built against — re-run the
script after bumping babeldoc. A stale zip is not an error: it simply isn't the
file `restore_offline_assets_package_async` looks for, and setup downloads
instead.

The HuggingFace embedding model for RAG chat (~470 MB) is still **not** bundled
and still downloads on first use to `~/.cache/huggingface` — now as
`onnx/model.onnx` + `tokenizer.json` fetched by `rag/onnx_embeddings.py`, rather
than by sentence-transformers.

Releases are built by `.github/workflows/release.yml` on a `v*` tag: it stages
the offline assets, runs `pnpm tauri build`, and attaches the installer to a
**draft** release. Signing is opt-in — set the `WINDOWS_SIGN_COMMAND` secret
(Azure Trusted Signing) or `bundle.windows.certificateThumbprint` (an OV cert in
the runner's store); with neither, the job warns and ships unsigned.

Hidden-import additions for chromadb / babeldoc / etc. live in
`pdfusion-sidecar.spec`. Extend that file (then rerun `build-sidecar.ps1`)
when the bundled exe raises `ModuleNotFoundError` at startup.

## Logs

Both streams land in `~/AppData/Local/PDFusion/logs/`, rotating at 5 MB with 5
backups kept:

- **Python sidecar** → `app.log`, via `utils/logging_setup.py::configure_logging`
  — one shared, `RotatingFileHandler`-backed setup called from all three ways
  the sidecar can start: `main.py` (the bundled `pdfusion-sidecar.exe`, and a
  hand-run `python main.py`), `python -m desktop_pdf_translator.api.server`
  (what `pnpm tauri dev` actually spawns), and the `pdfusion-sidecar` console
  script (`server.py::main` directly, via `pyproject.toml`'s
  `[project.scripts]`). `main.py` calls it before importing `server.py`, so an
  import failure there still lands in `app.log` rather than a windowed app's
  nonexistent stderr; `server.py::main` calls the same function, and
  `force=True` on `logging.basicConfig` makes that second call — when both run
  in one process — a harmless no-op re-application of the same handlers.
- **Rust shell** → `shell.log`, via `tauri_plugin_log` (`lib.rs::run`),
  default level `info`, plus a stdout target so `log::info!`/`warn!` —
  including the `[sidecar stdout]` / `[sidecar stderr]` relays — still show up
  in the `pnpm tauri dev` terminal. This replaced a bare `env_logger::try_init()`
  with no file target, which meant a release build (`main.rs` sets
  `windows_subsystem = "windows"`, so there's no console) discarded that
  stream entirely — "Sidecar failed to start" had nowhere to go.

The boot-error screen's "Show logs folder" button (`open_logs_folder` in
`lib.rs`) opens this directory regardless of which file(s) exist yet.

The sidecar's bearer token is `print`ed to stdout, not logged, so it was never in
`app.log`; `sidecar.rs:redact_ready_line` keeps it out of both the dev terminal
and `shell.log`.

## Tests and code quality

- **What is covered, and what still isn't.** The PDF-export path, the language
  contract, key storage and config read/write, the job registry, both SQLite
  caches, Argos's batching, and translator failure/retry accounting. Still
  uncovered: the BabelDOC pipeline in `processors/processor.py` proper, and all
  of `rag/`. If you touch those, expect to write tests from scratch. On the
  frontend, the PDF viewer's pure half is covered (`lib/pdf-viewer/`: geometry,
  find matching, the artifact change log, shortcut mapping). Its DOM half is
  not (`page-renderer.ts`, text layers, find highlighting, drag and drop),
  because vitest runs in node with no DOM. Exercise that half in
  `pnpm tauri dev`.

  ```bash
  # Python (pytest config lives in pyproject.toml; tests/conftest.py puts src/ on sys.path)
  python -m pytest tests           # test_file_export.py, test_pdf_export_api.py,
                                   # test_translate_language_contract.py,
                                   # test_translation_failure_reporting.py,
                                   # test_translation_resilience.py,
                                   # test_config_security.py, test_config_manager_load.py,
                                   # test_cors_origins.py, test_sidecar_boot.py,
                                   # test_engine_assets.py, test_setup_api.py,
                                   # test_translate_preflight.py, test_job_registry.py,
                                   # test_translation_cache_store.py, test_pdf_cache_store.py,
                                   # test_argos_batching.py,
                                   # test_doc_layout_cache.py, test_engine_warm_gate.py,
                                   # test_export_openapi.py, test_sse_schemas.py,
                                   # test_sbd_compat.py, test_onnx_embeddings.py
  python -m pytest tests -m smoke  # test_sidecar_smoke.py — excluded by default

  # Frontend (vitest, node environment — no jsdom)
  cd desktop && pnpm test          # src/**/*.test.ts

  # Rust shell (argv parsing in lib.rs, sidecar helpers in sidecar.rs)
  cd desktop/src-tauri && cargo test
  ```

  **Everything in the default run is in-process, and that is a property the
  suite defends rather than a happy accident.** It finishes in ~10 s, most of
  which is `test_sidecar_boot.py`'s subprocess probes and the provider SDKs
  `test_translation_resilience.py` imports on purpose. It used to cost ~13 s
  for a far smaller suite, because importing **anything** under
  `desktop_pdf_translator.api` pulled in BabelDOC and torch; see "Import cost
  is a startup budget" above for the rule that fixed it. `test_sidecar_boot.py`
  is the guard — it asserts in a subprocess that importing `api.server` leaves
  torch, chromadb, stanza, BabelDOC, sklearn and camelot out of `sys.modules`.
  Every name on that list has to be **installed**, which a second test enforces:
  an uninstalled one cannot be imported at boot, so guarding it proves nothing
  and only hides a typo. That is why `transformers` and `sentence_transformers`
  left the list when the excludes took them out of the dependency tree, while
  torch and stanza stayed. If it goes red, the desktop app's startup
  is what broke; a slow suite is only the symptom you notice first.

  Two conventions that keep it that way, both worth preserving:

  - **A cache test builds its own cache under `tmp_path`.** Every storage class
    here is a process-wide singleton over `~/AppData/Local/PDFusion/`, so a
    test that reaches for `get_pdf_cache()` reads and evicts the developer's
    own data. The same applies to settings: `_refresh_cap_from_settings` and
    `_cache_enabled` are stubbed rather than allowed to find a real
    `config.toml`.
  - **`tests/test_sidecar_smoke.py` is marked `smoke` and deselected by
    `addopts`.** It is the one suite that spawns real interpreters. It runs
    twice — once against `python -m desktop_pdf_translator.api.server`, once
    against the staged PyInstaller exe, which *skips* when none is built. The
    frozen half is the only thing that can catch a `ModuleNotFoundError` from
    the spec's `excludes` list, and it will also fail against a **stale**
    staged exe, which reads identically. Check the exe's timestamp before
    believing it.
- **`ruff` is the lint gate**, declared in
  `pyproject.toml [project.optional-dependencies].dev` and configured under
  `[tool.ruff]`. It runs its default rule set — pycodestyle errors plus
  pyflakes (`E4`, `E7`, `E9`, `F`) — and the tree is clean, so `ruff check src
  tests` is expected to pass. It is deliberately not widened: line length is
  left to `black`, which is *not* wired into CI, because reflowing the existing
  prose comments would bury every real diff. black / isort / flake8 / mypy stay
  installed for local use and are not gates. There is no pre-commit and no
  Makefile.
- **TypeScript** is checked by `pnpm build` (which runs `tsc` before `vite build`). There is no separate lint step (no ESLint config).
- **CI** is `.github/workflows/ci.yml`, on every PR and push to `main`, and
  every job runs on `windows-latest` — the key store is DPAPI, the sidecar is
  found through `%LOCALAPPDATA%`, and the shell uses a Job Object, so a Linux
  runner would skip or mis-test all three. Two gating jobs: `python`
  (`ruff check` → `pytest` → regenerate + diff-check `openapi.json` →
  `pytest -m smoke`) and `desktop` (`pnpm test` → check `api-types.d.ts` is
  current → `pnpm build` → `build-sidecar.ps1 -Stub` →
  `cargo check --all-targets` → `cargo test`).

  Two things about it that are easy to get wrong on a rewrite:

  - **The Python job installs `requirements.txt`, not just the package.** The
    two are not in lockstep on purpose, and
    `test_sidecar_boot.py:test_every_forbidden_name_is_a_real_module` asserts
    torch / chromadb / camelot & co. are *installed but not imported* — a
    partial install turns the boot guard into a pass for the wrong reason.
  - **The desktop job runs `pnpm build` before touching cargo.** Tauri's build
    script validates `externalBin` and `resources` at compile time and needs
    `frontendDist` (`desktop/dist/`) to exist, so `cargo check` fails on a
    fresh checkout until both the frontend is built and a sidecar is staged.
    `-Stub` covers the second in seconds.

  A third job, `frozen-sidecar`, runs the real PyInstaller build and then the
  smoke tests against the exe. It is `workflow_dispatch` only: it costs 10-20
  minutes, and it is the check to run before cutting an installer or after
  changing `pdfusion-sidecar.spec`.

## Out of scope (for a later phase)

- **Auto-update** flow.
- **A signing certificate.** The plumbing exists — `bundle.windows` carries
  `digestAlgorithm`/`timestampUrl` and `release.yml` reads a
  `WINDOWS_SIGN_COMMAND` secret — but no certificate is configured, so shipped
  installers are unsigned and SmartScreen warns on first install.
- **Cross-platform** (macOS/Linux) — Tauri supports both, but explicit testing deferred. The PyInstaller spec is Windows-tested only.
- **i18n of the UI strings** (the UI itself stays English; the translation *output* follows the toolbar's target language).
- **More Argos language pairs** — the offline backend ships en→vi only. Adding
  a pair means shipping/downloading its pack, then extending `SUPPORTED_PAIRS`
  in `translators/capabilities.py`.
- **Pre-bundled ML assets for RAG** — the HuggingFace embedding model (~470 MB)
  still downloads on first Chat use, with no progress and no preflight. The
  translation side of that problem is solved (see "First-run engine setup");
  Chat's half of issue #21 is deliberately left for a follow-up.
- **Synchronized scrolling between the Original and Translated panes** — the
  first item in issue #30, left out on purpose when the rest of that issue
  shipped. The panes scroll and zoom independently. `lib/pdf-viewer/layout.ts`'s
  `scrollAnchor` / `offsetForAnchor` (page + fraction) are the pieces a sync
  would build on.
- **Auto-save preference** — saving a translation is an explicit action (Save dialog). A "always save `<name>_vi.pdf` beside the source" setting was proposed in issue #11 but deliberately not built: it needs a config field, a Settings control, and an overwrite policy for repeat runs.

## Removed (legacy)

- The old PySide6 / qfluentwidgets GUI in `src/desktop_pdf_translator/gui/` has been deleted along with its deps (`PySide6`, `PySide6-Fluent-Widgets`, `QtAwesome`) from `requirements.txt`. If you need to resurrect the legacy GUI for any reason, pin those three packages back and recover `gui/` from git history (it lived through commit `139d977` "feat: migrate UI from PySide6 to Tauri 2 + React + FastAPI sidecar").
