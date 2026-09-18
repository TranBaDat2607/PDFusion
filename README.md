# PDFusion

Desktop app for translating PDFs (default target: Vietnamese) while preserving layout, plus an optional RAG chat to ask questions about the loaded document.

Windows and Linux are supported and built by CI; macOS builds from source but is
not packaged yet (see [Platform support](#platform-support)).

> **Architecture rewrite** — The UI is now built with **Tauri (Rust shell) + React + TypeScript + Tailwind + shadcn/ui**. The Python translation/RAG backend is unchanged and runs as a **FastAPI sidecar** spawned by the Tauri shell on app start.

## Platform support

| | Windows | Linux | macOS |
|---|---|---|---|
| Runs from source | yes | yes | yes, untested |
| Packaged by CI | `.exe` (NSIS) | `.deb`, `.AppImage` | not yet |
| API keys stored in | DPAPI | Secret Service (gnome-keyring, KWallet) | Keychain |
| Data root | `%LOCALAPPDATA%\PDFusion` | `$XDG_DATA_HOME/PDFusion`, else `~/.local/share/PDFusion` | `~/Library/Application Support/PDFusion` |

macOS is wired up — a `dmg` target, the Keychain and the data root are all in
place — but nobody has run it on a Mac, and an unsigned, un-notarized `.dmg` is
refused by Gatekeeper, so no macOS artifact is published. See issue #69.

**On Linux with no keyring running** (a bare tiling-WM setup, a container), keys
fall back to an obfuscated value in `config.toml` that anyone who can read the
file can recover. The app says so in `app.log` when it happens. Install and
unlock `gnome-keyring` or `kwalletmanager` if that matters to you.

## Prerequisites

- **Python** 3.11 (via Anaconda/Miniforge) — for the FastAPI sidecar
- **Node.js** ≥ 18 + **pnpm** — for the React frontend
- **Rust** (rustup, cargo) — for the Tauri shell
- **Ghostscript** — optional, only needed by Camelot for table extraction during RAG indexing (pdfplumber fallback runs without it)

Per platform:

- **Windows** — Microsoft Visual C++ Build Tools 2022/2026 (the Rust MSVC
  linker), and the WebView2 Runtime (ships with Windows 11; install separately
  on Windows 10).
- **Linux** — webkit2gtk **4.1** (not 4.0, which is Tauri 1) and its build
  headers. On Debian/Ubuntu:

  ```bash
  sudo apt install libwebkit2gtk-4.1-dev libgtk-3-dev \
      libayatana-appindicator3-dev librsvg2-dev patchelf \
      build-essential curl wget file libssl-dev libxdo-dev
  ```

  Fedora: `webkit2gtk4.1-devel gtk3-devel libappindicator-gtk3-devel librsvg2-devel patchelf`.
  Arch: `webkit2gtk-4.1 gtk3 libappindicator-gtk3 librsvg patchelf`.
- **macOS** — Xcode command line tools (`xcode-select --install`). WKWebView is
  part of the OS.

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
> box. The Tauri shell auto-detects either name under `anaconda3`,
> `miniconda3` or `miniforge3` in your home directory, on every platform. If
> you use something else, set `PDFUSION_PYTHON` to that env's interpreter
> (`python.exe` on Windows, `bin/python` elsewhere) before launching the
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

### 4. Local and OpenAI-compatible models (optional)

The **OpenAI** and **Claude** tabs in Settings each have an **Endpoint** field,
so PDFusion can translate with a model running on your own machine, or through
a proxy that speaks one of those APIs. Leave it blank to use the provider
itself.

| Server | Settings tab | Endpoint | API key | Model |
|---|---|---|---|---|
| [Ollama](https://ollama.com) | OpenAI | `http://localhost:11434/v1` | any text, e.g. `ollama` | a name from `ollama list` |
| Ollama (recent versions) | Claude | `http://localhost:11434` | any text | a name from `ollama list` |
| [LM Studio](https://lmstudio.ai) | OpenAI | `http://localhost:1234/v1` | any text | the model identifier LM Studio shows |

Then pick that service in the toolbar. Worth knowing:

- **Enter the key together with the endpoint.** A saved key is only ever sent
  to the endpoint it was saved for, so changing the endpoint asks for the key
  again. Local servers ignore the key, but the field can't be empty. An
  `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` from `.env` or your environment is
  only used with the provider itself.
- **Save checks the model with the server first.** If the server isn't running
  yet, Save shows the error and offers **Save anyway**.
- **The model field takes any name.** The list beside it only makes
  suggestions.
- Chat writes its answers with the same service and endpoint.

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

## Building an installer

Same three steps on every platform; only the script extension differs.

```bash
# 1. Install Python deps + PyInstaller (not in requirements.txt — it's a dev extra).
conda activate pdfusion
pip install -r requirements.txt
pip install -e ".[dev]"          # or just: pip install pyinstaller

# 2. (Optional but recommended) Stage the ~290 MB of engine assets the
#    installer ships, so it is offline-ready. Without this the app downloads
#    them on the user's first translate instead.
./fetch-offline-assets.sh        # Windows: ./fetch-offline-assets.ps1

# 3. Install frontend deps and build.
cd desktop
pnpm install
pnpm tauri build
```

The Tauri bundler auto-runs the build-sidecar script (via the
`beforeBundleCommand` in each `tauri.<platform>.conf.json`), which invokes
PyInstaller against `pdfusion-sidecar.spec` and then stages the result. *Where*
it stages it differs, because PyInstaller's one-dir bootloader requires
`_internal/` to sit next to the executable and the bundlers put binaries and
resources in different places:

| | Staged to | Shipped as |
|---|---|---|
| Windows | `desktop/src-tauri/binaries/` + `desktop/src-tauri/_internal/` | `externalBin` + a `resources` glob, both at the install root |
| Linux, macOS | `desktop/src-tauri/sidecar/` (the whole tree) | one `resources` directory |

Output:
```
# Windows
desktop/src-tauri/target/release/bundle/nsis/PDFusion_<version>_x64-setup.exe
# Linux
desktop/src-tauri/target/release/bundle/deb/PDFusion_<version>_amd64.deb
desktop/src-tauri/target/release/bundle/appimage/PDFusion_<version>_amd64.AppImage
```

Notes:
- **Windows installs per user** — under `%LOCALAPPDATA%\Programs\PDFusion`, with
  no admin rights and no UAC prompt. NSIS is the only Windows bundle target; the
  per-machine WiX `.msi` was dropped, so re-add `"msi"` to `bundle.targets` in
  `tauri.windows.conf.json` if you need one for an IT deployment.
- **The `.deb` depends on `libwebkit2gtk-4.1-0` and `libgtk-3-0`** (derived by
  the bundler, not hand-listed); the AppImage carries its own copies.
- **First build is slow** — ~10–20 min, because PyInstaller bundles the full
  chromadb + babeldoc stack.
- **The installer is large** — ~470 MB, most of which is the ~290 MB of engine
  assets it ships so the app works offline on first run. The RAG embedding
  weights (~470 MB) still download lazily on first Chat use to
  `~/.cache/huggingface`.
- **Installers are unsigned by default** — Windows SmartScreen warns on first
  install; the Linux bundles carry no signature either. See *Code signing*
  below.

### Code signing

`tauri.conf.json` carries the `digestAlgorithm` and `timestampUrl` half of the
configuration; what it deliberately does not carry is a certificate. Supply one
of the two and the release workflow signs:

- **Azure Trusted Signing** (no cert to store, billed per month) — set the
  repository secret `WINDOWS_SIGN_COMMAND` to the signing invocation, with `%1`
  standing in for the file being signed. `.github/workflows/release.yml` passes
  it through to `bundle.windows.signCommand`.
- **An OV/EV certificate in the runner's store** — set
  `bundle.windows.certificateThumbprint` instead.

With neither set the workflow prints a warning and produces an unsigned
installer, which is the current shipped state. SmartScreen keeps warning until
one of them is configured.
- **Dev iteration without a full PyInstaller build**: if you only want to
  hack on the React/Rust side and don't need a working bundled sidecar,
  run `./build-sidecar.sh --stub` (Windows: `./build-sidecar.ps1 -Stub`) once
  to drop placeholder files so `pnpm tauri dev` and `cargo check` succeed. The
  dev shell falls back to your local conda Python at runtime.

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

`app.log` (Python sidecar) and `shell.log` (Rust shell) are written to `logs/`
under the data root for your platform — see the table in
[Platform support](#platform-support). The boot screen's **Show logs folder**
button opens it, whichever one that is.
