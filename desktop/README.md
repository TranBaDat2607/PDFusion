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
`~/anaconda3/envs/pdfusion/python.exe` by default; override with
`PDFUSION_PYTHON=/path/to/python.exe pnpm tauri dev`.

## Build

```bash
pnpm tauri build
```

Output: `src-tauri/target/release/bundle/`.
