"""Shared filesystem locations under the user's AppData directory.

Stdlib-only — safe to import from anywhere on the sidecar's boot path,
including `main.py`'s pre-bootstrap logging setup and `api/server.py`'s
`main()` (see "Import cost is a startup budget" in CLAUDE.md).
"""

from __future__ import annotations

from pathlib import Path


def appdata_dir() -> Path:
    """`~/AppData/Local/PDFusion`, created if it doesn't exist yet."""
    d = Path.home() / "AppData" / "Local" / "PDFusion"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    """`~/AppData/Local/PDFusion/logs`, created if it doesn't exist yet."""
    d = appdata_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d
