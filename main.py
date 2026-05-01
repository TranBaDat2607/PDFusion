#!/usr/bin/env python3
"""Standalone runner for the PDFusion sidecar.

In production, the Tauri shell (`desktop/src-tauri`) spawns this same module
with `python -m desktop_pdf_translator.api.server`. During development you can
run `python main.py` to start the sidecar on its own and hit it with curl, or
to debug it before launching the desktop app.

For the full desktop UI, see `desktop/README.md` and run `pnpm tauri dev`
from the `desktop/` folder.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def _bootstrap_path() -> None:
    src = Path(__file__).parent / "src"
    if src.exists():
        sys.path.insert(0, str(src))


def _setup_logging() -> None:
    log_dir = Path.home() / "AppData" / "Local" / "PDFusion" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "app.log"),
            logging.StreamHandler(sys.stderr),
        ],
    )
    for noisy in ("urllib3", "requests", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> int:
    _bootstrap_path()
    _setup_logging()
    print(
        "Starting PDFusion sidecar standalone. For the full desktop UI run "
        "`pnpm tauri dev` from the desktop/ folder.",
        file=sys.stderr,
    )
    try:
        from desktop_pdf_translator.api.server import main as run_sidecar

        run_sidecar()
        return 0
    except KeyboardInterrupt:
        print("Sidecar interrupted by user", file=sys.stderr)
        return 0
    except Exception as exc:  # noqa: BLE001
        logging.exception("Fatal error starting sidecar")
        print(f"Fatal error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
