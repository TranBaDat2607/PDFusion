# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

**The reasoning behind every rule below — the bugs that produced it, the
measurements behind each constant — lives in
[`docs/architecture-notes.md`](docs/architecture-notes.md). Read the relevant
section there before changing anything this file flags.**

## Project overview

PDFusion is a desktop app that translates PDFs (default target: Vietnamese)
while preserving layout, plus an optional RAG chat over the loaded document.
BabelDOC is the translation engine.

Stack: **Tauri 2 (Rust shell) + React + TypeScript + Tailwind + shadcn/ui**,
with the Python translation/RAG/config code exposed as a **FastAPI sidecar**
the shell spawns at startup. (Migrated from PySide6/qfluentwidgets in 2026; the
old GUI is deleted — recover from git history at `139d977` if ever needed.)

Windows is the lead platform. **Linux is supported and gated by CI; macOS
builds but is unverified** (#69).

## Commands

```bash
# Desktop app (shell auto-spawns the sidecar)
cd desktop && pnpm tauri dev          # dev with HMR
cd desktop && pnpm tauri build        # production installer

# Frontend only (browser, no shell, no sidecar)
cd desktop && pnpm dev | pnpm build

# Sidecar only
conda activate pdfusion && python main.py
# → prints `READY port=<n> token=<n>`; docs at http://127.0.0.1:<n>/docs

# Tests
python -m pytest tests                # ~10 s, all in-process
python -m pytest tests -m smoke       # spawns real interpreters; deselected by default
cd desktop && pnpm test               # vitest, node env (no jsdom)
cd desktop/src-tauri && cargo test
ruff check src tests scripts          # the lint gate

# After touching api/schemas.py, api/sse_schemas.py or any route:
python -m desktop_pdf_translator.api.export_openapi --output desktop/src/lib/openapi.json
cd desktop && pnpm run generate:api-types
```

## Setup

```bash
conda create -n pdfusion python=3.11.14 && conda activate pdfusion
pip install -r requirements.txt       # canonical: flat install of the rag+advanced extras
cd desktop && pnpm install
```

- `requirements.txt` and `pyproject.toml` are **not** in lockstep — the extras
  (`rag`, `advanced`, `all`) live in pyproject. For a fully working app use
  `requirements.txt` or `pip install -e ".[all]"`.
- The shell auto-detects conda envs named `pdfusion`/`pdfusion-env` under
  `anaconda3`/`miniconda3`/`miniforge3`; otherwise set `PDFUSION_PYTHON` to your
  interpreter.
- External deps: Rust toolchain; the system webview (**Windows** WebView2 +
  MSVC Build Tools; **Linux** webkit2gtk **4.1** — 4.0 does not satisfy the
  build; **macOS** Xcode CLT); Ghostscript optionally, for Camelot.
- API keys: `.env` at the repo root (`OPENAI_API_KEY`, `GEMINI_API_KEY`,
  `ANTHROPIC_API_KEY`) or the in-app Settings sheet, which encrypts them into
  `config.toml` (DPAPI on Windows, an OS-keystore-backed Fernet key elsewhere,
  machine-id-derived as a last resort).

## Architecture

```
Tauri shell (Rust, desktop/src-tauri/)
 ├ spawns + supervises the Python sidecar, kills it on exit
 ├ native dialogs, open/reveal a PDF, the data root
 └ WebView: React + Vite + Tailwind + shadcn/ui
    ├ TanStack Query (server state) · Zustand (UI state)
    ├ pdf.js viewer · react-markdown + KaTeX + shiki (chat)
    └ Bearer-token HTTP/SSE → 127.0.0.1:<port>
        └ FastAPI sidecar (src/desktop_pdf_translator/api/)
           uvicorn on an ephemeral port, SSE job streams,
           wraps PDFProcessor / RAG / ConfigManager
```

The sidecar prints one handshake line, `READY port=… token=…`, which the shell
parses and forwards to the webview via `sidecar://ready`.

**Long-running work (translate, index, ask) follows one pattern:** `POST`
returns `{job_id}`; the work runs in a background task appending to the job's
event `history`; `GET …/events?last_seq=N` replays what was missed then tails
live; a `done`/`error`/`cancelled` event closes the stream. A job survives a
dropped connection. `/setup/engine` is the exception — it is a process-wide
singleton with no job id, so it is polled via `GET /setup/status`.

### Module layout

| Path | Responsibility |
|---|---|
| `desktop/src-tauri/src/lib.rs` | Builder, plugins, sidecar spawn/shutdown, app commands |
| `desktop/src-tauri/src/sidecar.rs` | Python locate, child process, READY parsing, health poll, data root |
| `desktop/src-tauri/tauri.conf.json` | Shared config only — window, CSP, icons |
| `desktop/src-tauri/tauri.{windows,linux,macos}.conf.json` | Per-platform packaging, merged over the above |
| `scripts/build_sidecar.py`, `scripts/fetch_offline_assets.py` | All build logic; `build-sidecar.{ps1,sh}` etc. only resolve an interpreter |
| `desktop/src/components/` | `layout/`, `pdf-viewer/`, `chat/`, `settings/`, `translation/`, `setup/`, `ui/` (shadcn) |
| `desktop/src/lib/` | `api-client.ts`, `sse.ts`, `store.ts`, plus the pure, unit-tested helpers (`pdf-viewer/`, `export-pdf.ts`, `page-range.ts`, `service-settings.ts`, …) |
| `desktop/src/lib/{openapi.json,api-types.d.ts}` | Generated + checked in; CI fails if stale |
| `desktop/src/hooks/` | `useSidecar`, `useConfig`, `useTranslation`, `useRagIndex`, `useRagAsk`, `useChatHistory`, `useExportTranslated`, `useTranslationEstimate` |
| `api/server.py`, `auth.py`, `jobs.py`, `routes/*`, `schemas.py`, `sse_schemas.py` | FastAPI app, bearer auth, job registry, routes, wire models |
| `engine_assets.py` | Single source of truth for "the offline engine is installed" |
| `config/` | `ConfigManager` + Pydantic `AppSettings` |
| `processors/` | `PDFProcessor` (BabelDOC), `page_selection.py`, `pdf_pages.py`, `pdf_cache.py`, `doc_layout_cache.py` |
| `translators/` | `BaseTranslator` + OpenAI/Gemini/Anthropic/Argos, `factory.py`, `capabilities.py`, `rate_limiter.py`, `translation_cache.py`, `param_compat.py`, `usage_estimate.py` |
| `rag/` | `EnhancedRAGChain`, `vector_store.py`, `index_spec.py`, `onnx_embeddings.py`, `keyword_search.py` |
| `storage/` | `sqlite.py` (pragmas, per-thread connections), `migrations.py`, `records.py` (`pdfusion.db`) |
| `utils/` | `encryption.py`, `file_export.py`, `logging_setup.py`, `paths.py` |

### HTTP API

Everything but `GET /health` requires `Authorization: Bearer <token>`. The
schema is checked in at `desktop/src/lib/openapi.json` — read it there rather
than re-deriving from the routes.

`/health` · `/auth/ping` · `GET|PUT /config` · `POST /config/validate` ·
`GET /config/options` · `GET|DELETE /config/cache` ·
`GET /setup/status` · `POST /setup/engine` ·
`POST /translate` + `/translate/{id}/events` + `/cancel` + `POST /translate/estimate` ·
`POST /rag/index` + `/events` · `POST /rag/ask` + `/events` ·
`GET /rag/documents` · `DELETE /rag/document/{id}` ·
`GET|DELETE /rag/document/{id}/messages` · `POST /rag/reset` ·
`GET /pdf/file` · `POST /pdf/export`

Non-obvious bits of the contract:

- `/translate`'s `chunk_ready` arrives in **priority order, not page order** —
  accumulate with `lib/translation-progress.ts`; the denominator is
  `pages_to_translate`.
- A document's id is the **SHA-256 of its bytes**, derived by the sidecar.
- Retrieved chunks ride on `answer.pdf_references` (not `pdf_sources`), with
  1-indexed pages.
- There is deliberately no `output_dir` on `/translate`; output is always a
  per-job temp dir.

## Invariants

Each of these has a section in `docs/architecture-notes.md` and, unless noted,
a test. They are the things most easily undone by "simplifying".

**Startup**
- **A package `__init__.py` re-exports only names that are free to import.**
  Importing a submodule runs its parent's `__init__`; this rule took
  `import api.server` from 16.3 s to 0.7 s. `api/` and `rag/` re-export nothing.
  BabelDOC and the RAG stack are imported *inside* handlers, and every such
  import runs through `asyncio.to_thread` because a blocking import on the loop
  freezes every stream. `tests/test_sidecar_boot.py` guards this.
- Pre-warm threads may **materialize** an asset, never **download** one —
  downloading unattended can race and destroy the file the setup screen is
  showing a progress bar for.
- The sidecar prints READY before `create_app()`, so `main()` must repeat
  uvicorn's `if not server.started: sys.exit(...)` tail.

**Cross-platform** (#69)
- The data root is the platform's (`%LOCALAPPDATA%` / `~/Library/Application
  Support` / XDG). Two implementations exist (`utils/paths.py`,
  `sidecar.rs`); what keeps them in step is the shell exporting
  `PDFUSION_DATA_DIR` on **both** spawn paths.
- Off Windows the sidecar is **not** an `externalBin` — the bundlers split it
  from its `_internal/` tree. It ships as one resource directory.
- Packaging belongs in `tauri.<platform>.conf.json`, never the shared config.
- Build logic lives in `scripts/`; the `.ps1`/`.sh` launchers only find an
  interpreter. `windows-sys` is target-gated, so there is **no Job Object
  equivalent off Windows** — a `SIGKILL` of the shell orphans the sidecar.

**Config and keys**
- A saved API key is only ever sent to the endpoint it was saved for: `PUT
  /config` 422s an endpoint change without `api_key` in the same body.
- The keystore holds **one master key**, and the decrypt path never mints a
  replacement. Keys it could not read are remembered and written back verbatim,
  so a locked keyring doesn't erase every provider key on the next save.
- `config.toml` is written to a temp file and `os.replace`d; the `.bak` always
  has the keys stripped.
- A parameter a model refuses is dropped and recorded, not failed on
  (`param_compat.py`). Model suggestions are not a whitelist; retired ids are
  swapped on load (`RETIRED_MODELS`).

**Translation**
- **Nothing the pipeline writes is permanent.** Only `POST /pdf/export`
  produces a durable copy, so never label an unexported path "Saved to" —
  `translatedPdfPath` (ephemeral) and `exportedPdfPath` (permanent) stay apart.
- `exportedPdfPath` is cleared when a new artifact arrives, never at job start.
- `cancelling` is a real status; gate on `isTranslationBusy()`, not
  `status === "running"`. Workers check `task.cancelling()` before each page.
  (No unit test — exercise Cancel on a long run in `pnpm tauri dev`.)
- `export_pdf(protect=...)` refuses to overwrite the opened document, and the
  suggested name always appends `_<lang>`.
- **Translator failures are counted, not swallowed.** Every path that hands
  back source text goes through `BaseTranslator._handle_translation_error` —
  including the ones that never raise. A run with failed paragraphs is not
  cached; a fatal 401/403 aborts the job.
- Every SDK call goes through `_call_with_backoff` (clients are built with
  `max_retries=0`); transport errors are matched by class name along the MRO.
- `None` is the only way to say "unspecified" for languages; the default is
  applied in exactly one place, `capabilities.resolve_languages` (#12).
  `capabilities.py` is the single source of truth for what can run and stays
  free of heavy imports.
- `max_pages` limits the **selected** pages, not the document; the output is
  always the whole document, with unselected pages copied from the original.
- Two caches (whole-PDF, paragraph), both SQLite, content-addressed, versioned
  via `storage/migrations.py`. `is_cacheable_artifact` decides what may be
  written.

**Data and chat**
- Three stores under one root: `pdfusion.db` (system of record), `vectors/`
  (derived, rebuildable), the caches (disposable). All SQLite goes through
  `storage/sqlite.py`; schema changes are appended `Migration`s, never edits to
  a shipped one.
- Chat answers from exactly one document's ready index. An index is `ready`
  only once whole; `_recover` repairs what a dead process left. Never rebuild
  the chain or rerun `_recover` to pick up settings.
- A failure is an `error` event, never a fallback answer; retrieval that finds
  nothing never reaches the model.
- Changing the embedding model or chunker means editing `rag/index_spec.py`.

**PDF viewer** (#30)
- A slot's size comes from `lib/pdf-viewer/layout.ts`, never from a canvas;
  only visible pages ±3 hold one.
- A canvas is only ever replaced by a finished one; the translated pane swaps
  **pages**, not documents, driven by the `translatedChanges` log.
- Text layers are core `pdfjs.TextLayer` plus two pieces copied from
  `pdf_viewer.mjs` — re-check both when bumping pdfjs-dist.
- pdf.js fetches three asset directories at runtime — `wasm/`, `cmaps/`,
  `standard_fonts/` (#73, #77). Every prefix must end in `/`, the files keep
  their names, and the CSP needs `'wasm-unsafe-eval'`. `vite.config.ts` imports
  the directory map from `lib/pdf-viewer/asset-urls.ts` rather than repeating
  it. Two prefixes are inert without a companion flag: `cMapUrl` needs
  `cMapPacked: true`, and `standardFontDataUrl` needs `useSystemFonts: false` —
  the fixture tests must pass the app's flags or they prove a config that never
  runs. `useWorkerFetch` is pinned to `false` so one fetch path covers every
  platform, which is also why `iccUrl` is *not* set: it is unreachable under the
  pin. On Linux `lib.rs:enable_wasm_relaxed_simd` must run first in `run()` (#74).
- Shortcuts are owned by `MainLayout`'s capture-phase handler and are always
  `preventDefault`ed.

**Shell**
- `single-instance` is registered **first**, before every other plugin.
- Don't add Tauri plugins "just in case"; the capability grants are
  deliberately narrower than the plugin defaults.
- The sidecar's cwd is the data root, not the install dir.
- CORS dev origins come from the shell's `PDFUSION_DEV_ORIGINS`, not from
  `sys.frozen`.
- `restart_app` repeats the exit work by hand — anything added to the exit path
  must be added there too.
- CSP applies to the bundled app only, so **a CSP break first appears in
  `pnpm tauri build`**, not in dev.

**Argos ships without torch.** Three changes hold it: the repacked pack carries
MiniSBD, `chunk_type` is pinned by assignment, and `_sbd_compat` stubs
`stanza`. `pdfusion-sidecar.spec` then excludes stanza/torch/transformers;
the frozen half of `test_sidecar_smoke.py` is the only check that catches an
over-exclusion.

**The spec also prunes inside packages it keeps** (`_prune`). Two things there
look like dead weight and are not: `.pyi` stubs (skimage's `lazy_loader` parses
`skimage/__init__.pyi` at import) and hyperscan's sibling `hyperscan.libs/`.
Both fail as a *non-fatal* warm-up warning plus `cannot import name
'PDFProcessor'` on the first translate, and the smoke suite stays green through
either — only a real translate through the frozen exe catches them.

## Conventions

- **Comments answer *why*, not *what*.** The code already says what it does;
  a comment that restates it goes stale the moment the line changes. Write the
  reason instead: the constraint being honoured, the bug the shape prevents,
  the alternative that was tried and failed, the measurement behind a constant.
  If a line needs a *what* comment to be readable, rename or restructure it
  rather than annotating it. The existing prose comments in `src/` and
  `desktop/src/` are the model to follow — and the invariants above are exactly
  the kind of thing worth leaving at the call site.
- **Server state** → TanStack Query. **Ephemeral UI state** → Zustand
  (`lib/store.ts`). **Per-stream job state** → the job hooks, which update the
  store on terminal events. Chat on/off is server state (`config.rag.chat_enabled`).
- shadcn/ui is the component baseline (`pnpm dlx shadcn@latest add <name>`);
  Tailwind v4 tokens in `desktop/src/index.css`; accent green
  `oklch(0.689 0.179 142.51)`; `lucide-react` icons; `motion` for animation;
  `sonner` toasts; `<Dialog>` for confirmations, `<Sheet>` for settings.
- Frontend code imports the **generated** wire types
  (`import type { components } from "@/lib/api-types"`), never hand-written
  mirrors — three had already drifted before this existed (#13, #14, #27).
- Runtime config is `config.toml` under the data root; defaults and reference
  live in `config/default_config.toml`; `.env` overrides the TOML. Access via
  `get_config_manager()` / `get_settings()`.
- Logs: `logs/app.log` (sidecar) and `logs/shell.log` (Rust) under the data
  root, rotating at 5 MB × 5.
- `ruff` is the only Python gate and runs its default rule set; black/isort/
  mypy stay local. No pre-commit, no Makefile, no ESLint — TypeScript is
  checked by `pnpm build`.

## Building installers

```bash
conda activate pdfusion
./fetch-offline-assets.sh   # ~290 MB of engine assets; optional, app downloads otherwise
pip install -e ".[dev]"
./build-sidecar.sh          # PyInstaller one-dir. NOT optional — see below
cd desktop && pnpm tauri build
```

(`.ps1` instead of `.sh` on Windows.) Iterating on the installer itself? Swap
the last line for `pnpm run tauri:build:fast` — the same build with NSIS's
compressor switched to zlib, which takes `pnpm tauri build` from 607 s to 344 s
for an installer 68 MB larger.

Tauri validates `externalBin` and `resources` at **compile** time, before the
`beforeBundleCommand` that rebuilds the sidecar — so a fresh checkout fails
`cargo check`, `pnpm tauri dev` and `pnpm tauri build` until something is
staged. For frontend/Rust work use `./build-sidecar.sh --stub`; the shell
rejects anything under 1 MiB and falls back to local Python.

Targets: NSIS per-user on Windows, `deb` on Linux (**no AppImage** — linuxdeploy
cannot walk the PyInstaller tree; see the notes), `dmg`/`app` on macOS
(unverified). Releases are built by `.github/workflows/release.yml` on a `v*`
tag into a draft release.

## Tests and CI

Covered: PDF export, the language contract, key storage, config read/write and
the config API's key/endpoint rules, the job registry, both caches, Argos
batching, failure/retry accounting, param adaptation, the records DB, chat
isolation (real ChromaDB under `tmp_path`), chunking and rolling-PDF assembly,
the data root per platform, packaging config agreement, and the viewer's pure
half.

Not covered: `processors/processor.py` proper, most of `rag/` (extraction,
ranking, generation), and the viewer's DOM half (vitest runs in node) —
exercise those in `pnpm tauri dev`.

Two conventions that keep the default run in-process and ~10 s:

- **A cache test builds its own store under `tmp_path`.** `tests/conftest.py`
  additionally pins `PDFUSION_DATA_DIR` to a throwaway folder and stubs out the
  keystore for the whole run. Don't remove either backstop.
- **`test_sidecar_smoke.py` is marked `smoke` and deselected**; it is the only
  suite that spawns real interpreters, and its frozen half skips when no exe is
  staged (and passes against a *stale* one — check the timestamp).

CI (`.github/workflows/ci.yml`, every PR and push to main): a `python` and a
`desktop` job, each a matrix over **windows-latest and ubuntu-latest** — each
platform is the only place its own half is exercised. The Python job installs
`requirements.txt` (not just the package), the desktop job runs `pnpm build`
before cargo, and the OpenAPI diff-check runs on Windows only. A third job,
`frozen-sidecar`, runs the real PyInstaller build and is `workflow_dispatch`
only — run it before cutting an installer or after touching the spec.

## Out of scope

Auto-update · a signing certificate (plumbing exists, no cert) · macOS
packaging and verification (Phase B of #69, needs hardware) · a Job Object
equivalent off Windows (`prctl(PR_SET_PDEATHSIG)`) · UI i18n · more Argos
language pairs · pre-bundled RAG embedding model (~470 MB, still downloads on
first Chat use) · synchronized scrolling between panes · an auto-save
preference (#11).
