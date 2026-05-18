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
- MSVC Build Tools 2022/2026 (Rust on Windows)

**Environment setup:**
```bash
conda create -n pdfusion python=3.11.14
conda activate pdfusion
pip install -r requirements.txt        # canonical install — pins all RAG + advanced deps
# Alternative: pip install -e ".[rag,advanced]"  (extras live in pyproject.toml)

cd desktop
pnpm install
```

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
Or use the in-app Settings sheet — keys are encrypted via `utils/encryption.py` before being written to `~/AppData/Local/PDFusion/config.toml`.

## Architecture

### Two-process model

```
┌─────────────────────────────────────────────────────────┐
│ Tauri shell (Rust) — desktop/src-tauri/                 │
│  • Spawns + supervises Python sidecar at startup        │
│  • Kills sidecar on app exit (RunEvent::ExitRequested)  │
│  • Exposes `sidecar_info` command to the React side     │
│  • Native dialogs (open/save), shell.openUrl, fs read   │
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
| `desktop/src/components/translation/` | `ProgressOverlay` |
| `desktop/src/components/ui/` | shadcn-generated primitives (button, dialog, sheet, …) |
| `desktop/src/lib/api-client.ts` | Typed HTTP wrapper with bearer-token + sidecar URL helpers |
| `desktop/src/lib/sse.ts` | Authenticated SSE reader (native EventSource can't set headers) |
| `desktop/src/lib/store.ts` | Zustand store for UI state |
| `desktop/src/hooks/` | `useSidecar`, `useConfig`, `useTranslation`, `useRagIndex`, `useRagAsk` |
| `src/desktop_pdf_translator/api/server.py` | FastAPI app + uvicorn entry + port discovery |
| `src/desktop_pdf_translator/api/auth.py` | Bearer-token middleware |
| `src/desktop_pdf_translator/api/jobs.py` | In-memory job registry + asyncio.Queue per job for SSE |
| `src/desktop_pdf_translator/api/routes/*` | `config`, `translation`, `rag`, `pdf` route modules |
| `src/desktop_pdf_translator/config/` | `ConfigManager` + Pydantic `AppSettings` (unchanged) |
| `src/desktop_pdf_translator/processors/` | `PDFProcessor` async generator wrapping BabelDOC (unchanged) |
| `src/desktop_pdf_translator/translators/` | `BaseTranslator`, OpenAI/Gemini/Anthropic/Argos + `TranslatorFactory` |
| `src/desktop_pdf_translator/rag/` | ChromaDB + `EnhancedRAGChain` + deep search (unchanged) |
| `src/desktop_pdf_translator/utils/` | API key encryption (unchanged) |

### HTTP API (sidecar)

All routes (except `GET /health`) require `Authorization: Bearer <token>`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness probe (no auth) |
| GET | `/auth/ping` | Auth probe — used by the Rust shell after startup |
| GET | `/config` | Current settings (API keys masked) |
| PUT | `/config` | Update API keys / models / language defaults |
| POST | `/config/validate` | Test a key by spinning up a translator + calling its `validate_configuration()` |
| GET | `/config/options` | Static dropdown data (languages, services, models) |
| POST | `/translate` | Start translation job → returns `{ job_id }` |
| GET | `/translate/{job_id}/events` | SSE: `progress`, `done`, `error`, `cancelled` |
| POST | `/translate/{job_id}/cancel` | Cancel an in-flight translation |
| POST | `/rag/index` | Index a PDF into ChromaDB → returns `{ job_id }` |
| GET | `/rag/index/{job_id}/events` | SSE: `progress`, `done`, `error` |
| POST | `/rag/ask` | Ask the RAG chain → returns `{ job_id }` |
| GET | `/rag/ask/{job_id}/events` | SSE: `progress`, `answer`, `done`, `error` |
| DELETE | `/rag/document/{document_id}` | Remove an indexed document from the vector store |
| GET | `/pdf/file?path=...` | Stream a PDF from disk (used by pdf.js client-side) |

### Long-running jobs (SSE pattern)

Long-running endpoints (translate, index, ask) follow the same pattern:
1. `POST /resource` returns `{ job_id }` immediately.
2. The actual work runs in a background asyncio task that pushes events into an `asyncio.Queue` keyed by `job_id`.
3. `GET /resource/{job_id}/events` opens an SSE stream that drains that queue.
4. A terminal event (`done`, `error`, or `cancelled`) closes the stream.

This replaces the previous `QThread + new asyncio loop` pattern from the PySide6 GUI.

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
   - **Process-lifetime translation cache.** A FIFO-evicted `dict` (cap 20k entries) memoizes paragraph translations across jobs in the same sidecar run, since Argos is deterministic.

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
- `.env` (project root) is auto-loaded via `python-dotenv` and overrides the TOML.
- Singleton: `get_config_manager()` / `get_settings()` from `desktop_pdf_translator.config`.

## Tauri shell details

- **Plugins enabled**: `opener` (open external URLs), `dialog` (file picker), `shell`, `fs` (allow reading `*.pdf`).
- **Window**: 1400×900 default, min 1024×700.
- **CSP**: currently `null` for dev. Tighten before bundling for distribution.
- **Sidecar lifecycle** is wired in `lib.rs::run()`'s `setup` and the `RunEvent::ExitRequested` handler kills the child process.
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

Application logs are written to `~/AppData/Local/PDFusion/logs/app.log`.

## Tests and code quality

- **There are currently no tests in this repo** — no `tests/` directory, no `test_*.py` files. `pyproject.toml` lists `pytest` / `pytest-cov` under the `dev` extra, but nothing is wired up. Don't waste time looking for an existing test suite; if you add one, set up `tests/` from scratch.
- **Python lint/format** tools are declared in `pyproject.toml [project.optional-dependencies].dev` (black line-length 88, isort with black profile, flake8, mypy) but the project has **no** pre-commit, no Makefile, and no CI. Run them manually if you want: `black src/ && isort src/`.
- **TypeScript** is checked by `pnpm build` (which runs `tsc` before `vite build`). There is no separate lint step (no ESLint config).
- **No CI**: `.github/workflows/` does not exist. All checks are local.

## Out of scope (for a later phase)

- **Auto-update** flow.
- **Code signing** for Windows (SmartScreen will warn on first install of the unsigned `.msi`).
- **CSP tightening** — `tauri.conf.json` still has `"csp": null`.
- **Cross-platform** (macOS/Linux) — Tauri supports both, but explicit testing deferred. The PyInstaller spec is Windows-tested only.
- **i18n of the UI strings** (the UI itself stays English; only the translation *output* is Vietnamese).
- **Pre-bundled ML assets** (HuggingFace embedding model + Argos en→vi pack) — currently both download on first use. Bundle them later for true offline-first.

## Removed (legacy)

- The old PySide6 / qfluentwidgets GUI in `src/desktop_pdf_translator/gui/` has been deleted along with its deps (`PySide6`, `PySide6-Fluent-Widgets`, `QtAwesome`) from `requirements.txt`. If you need to resurrect the legacy GUI for any reason, pin those three packages back and recover `gui/` from git history (it lived through commit `139d977` "feat: migrate UI from PySide6 to Tauri 2 + React + FastAPI sidecar").
