# PDFusion

Windows desktop app for translating PDFs (default target: Vietnamese) while preserving layout, plus an optional RAG chat to ask questions about the loaded document.

> **Architecture rewrite** — The UI is now built with **Tauri (Rust shell) + React + TypeScript + Tailwind + shadcn/ui**. The Python translation/RAG backend is unchanged and runs as a **FastAPI sidecar** spawned by the Tauri shell on app start.

## Prerequisites

- **Python** 3.11 (via Anaconda) — for the FastAPI sidecar
- **Node.js** ≥ 18 + **pnpm** — for the React frontend
- **Rust** (rustup, cargo) — for the Tauri shell
- **Microsoft Visual C++ Build Tools 2022/2026** — required by the Rust MSVC linker on Windows
- **WebView2 Runtime** — ships with Windows 11; install separately on Windows 10
- **Ghostscript** — optional, only needed by Camelot for table extraction during RAG indexing (pdfplumber fallback runs without it)

## Setup

### 1. Python sidecar (one-time)

```bash
conda create -n pdfusion python=3.11.14
conda activate pdfusion
pip install -r requirements.txt
# Optional extras (editable install — the package isn't published to PyPI):
pip install -e ".[rag]"        # RAG chat
pip install -e ".[advanced]"   # table extraction
```

> These examples name the env `pdfusion`; `pdfusion-env` also works out of the
> box (the Tauri shell auto-detects either name). If you use something else,
> set `PDFUSION_PYTHON` to the env's `python.exe` path before launching the
> desktop app.

### 2. Tauri / React frontend (one-time)

```bash
cd desktop
pnpm install
```

> If bare `pnpm` isn't resolvable even after `corepack enable` (it can fail
> with `EPERM` writing shims into `Program Files\nodejs` without admin rights),
> install it globally instead: `npm install -g pnpm`.

### 3. API keys

Create a `.env` file in the project root:

```env
OPENAI_API_KEY=...
GEMINI_API_KEY=...
ANTHROPIC_API_KEY=...     # optional
```

You can also enter and validate keys later from the in-app **Settings** sheet
(they're encrypted before being written to disk).

## Running

### Full desktop app (recommended)

```bash
cd desktop
pnpm tauri dev
```

This builds the React UI (~10s) and the Rust shell (~5–10 min the first time;
seconds on subsequent runs), then opens the PDFusion window. The Tauri shell
will automatically spawn the Python sidecar from the conda env in the
background.

### Sidecar only (for debugging)

```bash
conda activate pdfusion
python main.py
```

This prints `READY port=<n> token=<n>` and then serves the FastAPI app on
`http://127.0.0.1:<n>`. OpenAPI docs are at `http://127.0.0.1:<n>/docs`.

## Building the Windows installer (.msi)

To produce a distributable `.msi` from a fresh clone:

```powershell
# 1. Install Python deps + PyInstaller (not in requirements.txt — it's a dev extra).
conda activate pdfusion
pip install -r requirements.txt
pip install -e ".[dev]"          # or just: pip install pyinstaller

# 2. (Optional but recommended) Drop the Argos en→vi language pack into
#    assets/argos/ so the installer ships offline-ready. Without it, the app
#    downloads ~80 MB on the user's first translate.
#    Download translate-en_vi.argosmodel from:
#      https://www.argosopentech.com/argospm/index/
#    Place it at: assets/argos/translate-en_vi.argosmodel

# 3. Install frontend deps and build.
cd desktop
pnpm install
pnpm tauri build
```

The Tauri bundler auto-runs `build-sidecar.ps1` (via `tauri.conf.json`'s
`beforeBundleCommand`), which invokes PyInstaller against
`pdfusion-sidecar.spec`, then stages the bundled sidecar into
`desktop/src-tauri/binaries/` and `desktop/src-tauri/_internal/`.

Output:
```
desktop/src-tauri/target/release/bundle/msi/PDFusion_<version>_x64_en-US.msi
```

Notes:
- **First build is slow** — ~10–20 min, because PyInstaller bundles the full
  chromadb + sentence-transformers + babeldoc stack.
- **The `.msi` is large** — ~500 MB to 1 GB. ML model weights download lazily
  on first use to `~/.cache/huggingface`.
- **The installer is unsigned** — Windows SmartScreen will warn on first
  install. Code signing is out of scope for the current phase.
- **Dev iteration without a full PyInstaller build**: if you only want to
  hack on the React/Rust side and don't need a working bundled sidecar,
  run `./build-sidecar.ps1 -Stub` once to drop placeholder files so
  `pnpm tauri dev` and `cargo check` succeed. The dev shell falls back to
  your local conda Python at runtime.

## Project layout

```
PDFusion/
├── desktop/                          ← Tauri + React frontend
│   ├── src/                          ← React + TypeScript
│   │   ├── components/               ← UI components (shadcn-based)
│   │   ├── hooks/                    ← TanStack Query + custom hooks
│   │   └── lib/                      ← API client, SSE, Zustand store
│   └── src-tauri/                    ← Rust shell, sidecar lifecycle
├── src/desktop_pdf_translator/
│   ├── api/                          ← FastAPI sidecar
│   ├── config/                       ← TOML + .env settings
│   ├── processors/                   ← BabelDOC translation pipeline
│   ├── translators/                  ← OpenAI / Gemini / Anthropic
│   ├── rag/                          ← ChromaDB + RAG chain + deep search
│   └── utils/                        ← API key encryption
├── main.py                           ← Standalone sidecar runner
└── requirements.txt
```

## Logs

Application logs are written to `~/AppData/Local/PDFusion/logs/app.log`.
