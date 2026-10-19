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


def main() -> int:
    _bootstrap_path()

    # Configured here (before importing server.py) so that even an import
    # failure in server.py — e.g. a PyInstaller `.spec` excludes gap — lands
    # in app.log rather than a windowed app's nonexistent stderr. server.py's
    # own main() calls the same function; force=True makes the second call a
    # harmless no-op re-application of the same handlers. See #26.
    from desktop_pdf_translator.utils import configure_logging

    configure_logging()
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
    # First, before main() can print READY or bind a port. A frozen sidecar is
    # its own `sys.executable`, so every multiprocessing child BabelDOC starts
    # (it saves PDFs in one) re-runs this exe with `--multiprocessing-fork`.
    # Without this each child booted a whole second sidecar instead of doing
    # its work, and every translate waited out the save's 120 s timeout.
    # A no-op in a source run.
    import multiprocessing

    multiprocessing.freeze_support()
    sys.exit(main())
