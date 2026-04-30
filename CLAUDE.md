# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PDFusion is a Windows desktop app for translating PDFs (default target: Vietnamese) while preserving layout/formatting. It uses BabelDOC as the translation engine and integrates an optional RAG (Retrieval-Augmented Generation) chat for asking questions about the loaded document.

The UI was migrated from PySide6/qfluentwidgets to **Tauri (Rust shell) + React + TypeScript + Tailwind + shadcn/ui** in 2026. The Python translation/RAG/config/utils modules are unchanged — they're now exposed as a **FastAPI sidecar** that the Tauri shell spawns at app startup.

## Running the Application

```bash
# Full desktop app (Tauri shell auto-spawns the sidecar):
cd desktop
pnpm tauri dev

# Sidecar only (for backend debugging):
conda activate pdfusion
python main.py
# → prints `READY port=<n> token=<n>`; OpenAPI docs at http://127.0.0.1:<n>/docs
```

> The local conda env is named `pdfusion` (single `f`). The Tauri shell looks
> for `~/anaconda3/envs/pdfusion/python.exe` by default; override with the
> `PDFUSION_PYTHON` environment variable if your env lives elsewhere.

**External system dependencies:**
- Ghostscript (BabelDOC PDF processing)
- Tesseract OCR (optional, scanned PDFs)
- WebView2 Runtime (ships with Windows 11)
- MSVC Build Tools 2022/2026 (Rust on Windows)

**Environment setup:**
```bash
conda create -n pdfusion python=3.11.14
conda activate pdfusion
pip install -r requirements.txt

cd desktop
pnpm install
```

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
| `src/desktop_pdf_translator/translators/` | `BaseTranslator`, OpenAI/Gemini/Anthropic + `TranslatorFactory` (unchanged) |
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
- **Python discovery** order: `PDFUSION_PYTHON` env var → `~/anaconda3/envs/pdfusion/python.exe` → `~/miniconda3/envs/pdfusion/python.exe` → `python` on PATH.

## Logs

Application logs are written to `~/AppData/Local/PDFusion/logs/app.log`.

## Out of scope (for a later phase)

- **PyInstaller bundling** of the sidecar into a single `.exe` so end users don't need conda.
- **Auto-update** flow.
- **Code signing** for Windows.
- **Cross-platform** (macOS/Linux) — Tauri supports both, but explicit testing deferred.
- **i18n of the UI strings** (the UI itself stays English; only the translation *output* is Vietnamese).

## Removed (legacy)

- The old PySide6 / qfluentwidgets GUI lived in `src/desktop_pdf_translator/gui/`. After verifying the Tauri UI works end-to-end, that folder will be deleted and these dependencies will leave `requirements.txt`: `PySide6`, `PySide6-Fluent-Widgets`, `QtAwesome`. They have already been removed from `requirements.txt` — pin them back temporarily if you need to run the legacy GUI.
