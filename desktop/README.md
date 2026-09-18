# PDFusion desktop shell

Tauri 2 + React + TypeScript + Tailwind + shadcn/ui frontend for PDFusion.
The Rust shell spawns the Python FastAPI sidecar (`src/desktop_pdf_translator/api/`)
at startup; the React UI talks to it over loopback HTTP + SSE.

See the project root [`CLAUDE.md`](../CLAUDE.md) for the full architecture.

## Develop

```bash
pnpm install
pnpm tauri dev
```

The shell looks for a `pdfusion` (or `pdfusion-env`) conda env under
`anaconda3`, `miniconda3` or `miniforge3` in your home directory — at
`envs/<name>/python.exe` on Windows and `envs/<name>/bin/python` everywhere
else. If yours is somewhere else, name it:

```bash
# Linux / macOS
PDFUSION_PYTHON=/path/to/env/bin/python pnpm tauri dev
```

```powershell
# Windows
$env:PDFUSION_PYTHON = "C:\path\to\python.exe"
pnpm tauri dev
```

To persist it across shells: `export PDFUSION_PYTHON=…` in your shell profile,
or on Windows `setx PDFUSION_PYTHON "C:\path\to\python.exe"` — `setx` only
affects *new* shells/processes, so open a fresh terminal before the next
`pnpm tauri dev`.

On Linux you also need webkit2gtk **4.1** and its headers before the Rust shell
will link — see the root [`README.md`](../README.md#prerequisites).

If bare `pnpm` isn't found even after `corepack enable` (it can fail with
`EPERM` writing shims into `Program Files\nodejs` without admin rights), install
it globally instead: `npm install -g pnpm`.

## Build

```bash
pnpm tauri build
```

Output: `src-tauri/target/release/bundle/` — `nsis/` on Windows,
`deb/` + `appimage/` on Linux, `macos/` + `dmg/` on macOS. Which targets are
built, and how the Python sidecar is staged for them, comes from
`src-tauri/tauri.<platform>.conf.json`, which Tauri merges over
`tauri.conf.json`.
