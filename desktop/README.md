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

The shell looks for the Python interpreter at
`~/anaconda3/envs/pdfusion/python.exe` by default (e.g. if your conda env has
a different name). Override it in PowerShell:

```powershell
$env:PDFUSION_PYTHON = "C:\path\to\python.exe"
pnpm tauri dev
```

To persist it across shells, use `setx PDFUSION_PYTHON "C:\path\to\python.exe"`
instead — `setx` only affects *new* shells/processes, so open a fresh terminal
before the next `pnpm tauri dev`.

If bare `pnpm` isn't found even after `corepack enable` (it can fail with
`EPERM` writing shims into `Program Files\nodejs` without admin rights), install
it globally instead: `npm install -g pnpm`.

## Build

```bash
pnpm tauri build
```

Output: `src-tauri/target/release/bundle/`.
