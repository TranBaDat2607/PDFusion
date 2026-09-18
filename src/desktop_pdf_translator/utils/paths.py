"""Shared filesystem locations for everything the sidecar persists.

Stdlib-only — safe to import from anywhere on the sidecar's boot path,
including `main.py`'s pre-bootstrap logging setup and `api/server.py`'s
`main()` (see "Import cost is a startup budget" in CLAUDE.md).

The function is still called `appdata_dir()` because that is what every store
imports, but the folder it names is only under AppData on Windows (#69):

| Platform | Data root |
|---|---|
| Windows | `%LOCALAPPDATA%\\PDFusion` |
| macOS | `~/Library/Application Support/PDFusion` |
| Linux / other | `$XDG_DATA_HOME/PDFusion`, else `~/.local/share/PDFusion` |

`PDFUSION_DATA_DIR` overrides all of it, on every platform. That is not a
developer convenience: the Tauri shell resolves the root itself
(`sidecar.rs:appdata_dir`), makes it the sidecar's cwd, pre-creates the layout
in it — and then exports it to the child through this variable. The two
resolvers implement the same rules so a hand-run sidecar lands in the same
place, but the export is what makes them *provably* agree rather than agreeing
by inspection.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_APP_DIR = "PDFusion"

#: Set by the Tauri shell on both spawn paths (`sidecar.rs:base_command`).
DATA_DIR_ENV = "PDFUSION_DATA_DIR"


def _legacy_appdata_dir() -> Path:
    """`~/AppData/Local/PDFusion` — where every Python store put its data
    before #59, whatever `%LOCALAPPDATA%` said.

    Off Windows this is a literal `AppData` folder in the user's home, which is
    where a from-source run on Linux or macOS landed before #69. Nothing ever
    shipped there, but a developer's config is worth carrying over — see
    `adopt_legacy_config`.
    """
    return Path.home() / "AppData" / "Local" / _APP_DIR


def _platform_data_dirs() -> list[Path]:
    """Candidate roots for this platform, best first.

    More than one, because the first entry can be unresolvable: `%LOCALAPPDATA%`
    can be unset or corrupt, and `Path.home()` itself can raise where neither
    `HOME` nor a passwd entry exists. Every caller treats the list as ordered
    preference and takes the first one it can create.
    """
    candidates: list[Path] = []
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / _APP_DIR)
        # The legacy home-based path is a real fallback on Windows rather than
        # only a migration source: it is where the app wrote before #59, so a
        # machine with a broken %LOCALAPPDATA% still finds its own data.
        candidates.append(_legacy_appdata_dir())
        return candidates

    if sys.platform == "darwin":
        candidates.append(Path.home() / "Library" / "Application Support" / _APP_DIR)
        return candidates

    # Linux and every other POSIX. XDG says relative paths in XDG_DATA_HOME are
    # invalid and must be ignored, which is why this is not a bare truthiness
    # check — a relative value would otherwise resolve against the sidecar's
    # cwd, and the shell sets that cwd to the data root itself.
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg and Path(xdg).is_absolute():
        candidates.append(Path(xdg) / _APP_DIR)
    else:
        candidates.append(Path.home() / ".local" / "share" / _APP_DIR)
    return candidates


def appdata_dir() -> Path:
    """The app's writable data root, created if it doesn't exist yet.

    Resolved the way the shell resolves it (`sidecar.rs:appdata_dir`):
    `$PDFUSION_DATA_DIR` if the shell exported one, then this platform's
    convention, then the temp dir so a caller is never handed a path it cannot
    write to.
    """
    candidates: list[Path] = []
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        candidates.append(Path(override))
    try:
        candidates.extend(_platform_data_dirs())
    except (OSError, RuntimeError):
        # `Path.home()` raises where the home directory can't be determined.
        pass
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

    Two ways to reach it. On Windows, where Local AppData has been relocated:
    `appdata_dir` resolves a different folder from the one the config was
    written to, and the first start after upgrading would otherwise run on
    defaults — API keys included. The keys are DPAPI-protected for the user,
    not for a path, so the copy still decrypts. Off Windows, where a
    from-source run predating #69 wrote to a literal `~/AppData/Local/PDFusion`
    that is nobody's convention; the keys there are Fernet values whose salt
    travels with them in the same file, so they decrypt after the copy too.

    A copy, never a move, and it never overwrites. Only the config comes along:
    the caches are disposable and the chat index rebuilds itself. `pdfusion.db`
    is deliberately *not* copied — it is a live SQLite database with its own
    WAL, and a byte copy of one of those is how you get a corrupt records
    store. Its old location is named in the log instead. Returns whether the
    config was copied.
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
    if (source.parent / "pdfusion.db").exists():
        logger.info(
            "Documents and saved chats from the previous location were left at "
            "%s; this build keeps its records in %s",
            source.parent,
            root,
        )
    return True
