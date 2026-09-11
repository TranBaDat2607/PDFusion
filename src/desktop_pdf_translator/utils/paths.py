"""Shared filesystem locations under the user's AppData directory.

Stdlib-only — safe to import from anywhere on the sidecar's boot path,
including `main.py`'s pre-bootstrap logging setup and `api/server.py`'s
`main()` (see "Import cost is a startup budget" in CLAUDE.md).
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_APP_DIR = "PDFusion"


def _legacy_appdata_dir() -> Path:
    """Where every Python store put its data before #59, whatever
    `%LOCALAPPDATA%` said."""
    return Path.home() / "AppData" / "Local" / _APP_DIR


def appdata_dir() -> Path:
    """The app's writable data root, created if it doesn't exist yet.

    Resolved the way the shell resolves it (`sidecar.rs:appdata_dir`):
    `%LOCALAPPDATA%\\PDFusion`, then `~/AppData/Local/PDFusion`, then the temp
    dir. The shell pre-creates the layout there and makes it the sidecar's cwd,
    so Python has to agree. It used to hardcode the home-based path, which is a
    different folder wherever Local AppData has been relocated (#59).
    """
    local = os.environ.get("LOCALAPPDATA")
    candidates = [Path(local) / _APP_DIR] if local else []
    candidates.append(_legacy_appdata_dir())
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        return candidate
    return Path(tempfile.gettempdir())


def logs_dir() -> Path:
    """`logs/` under `appdata_dir()`, created if it doesn't exist yet."""
    d = appdata_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def adopt_legacy_config(root: Path) -> bool:
    """Copy `config.toml` over from the legacy root when `root` has none.

    Only reachable where `%LOCALAPPDATA%` is not under the home folder. There,
    `appdata_dir` now resolves a different folder from the one the config was
    written to, and the first start after upgrading would otherwise run on
    defaults — API keys included. The keys are DPAPI-protected for the user,
    not for a path, so the copy still decrypts.

    A copy, never a move, and it never overwrites. Only the config comes along:
    the caches are disposable, and the chat index rebuilds itself. Returns
    whether the config was copied.
    """
    target = root / "config.toml"
    source = _legacy_appdata_dir() / "config.toml"
    if target.exists() or not source.is_file():
        return False
    for name in ("config.toml", "config.toml.bak"):
        src = source.with_name(name)
        dst = root / name
        if not src.is_file() or dst.exists():
            continue
        try:
            shutil.copy2(src, dst)
        except OSError as exc:
            logger.warning("Could not adopt %s from %s: %s", name, src.parent, exc)
            if name == "config.toml":
                return False
    logger.info("Adopted the configuration left at %s", source.parent)
    return True
