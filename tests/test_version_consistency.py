"""All version numbers that ship in a build must agree.

Five independent files each declared "the" PDFusion version before issue #25:
`pyproject.toml`, `desktop/src-tauri/tauri.conf.json`, `desktop/package.json`,
`desktop/src-tauri/Cargo.toml`, and the Python package's own `__version__`.
Nothing keeps them in sync except this test — bump the version everywhere it
reads from, or it fails with a diff of which source is behind.

Two more copies existed and were deleted as dead weight rather than kept in
sync: `AppSettings.version` (read in exactly one place, `api/server.py`'s
`create_app()`, which now reads `__version__` directly like `/health` already
did) and `src/desktop_pdf_translator/rag/__init__.py`'s own `__version__`
(never read anywhere). `config/default_config.toml`'s copy disappeared with
the rest of that file — it was bundled by PyInstaller but never actually read
by `ConfigManager`.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def _tauri_conf_version() -> str:
    path = _ROOT / "desktop" / "src-tauri" / "tauri.conf.json"
    return json.loads(path.read_text(encoding="utf-8"))["version"]


def _package_json_version() -> str:
    path = _ROOT / "desktop" / "package.json"
    return json.loads(path.read_text(encoding="utf-8"))["version"]


def _cargo_toml_version() -> str:
    path = _ROOT / "desktop" / "src-tauri" / "Cargo.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return data["package"]["version"]


def _package_dunder_version() -> str:
    from desktop_pdf_translator import __version__

    return __version__


def test_all_version_sources_agree() -> None:
    versions = {
        "pyproject.toml": _pyproject_version(),
        "desktop/src-tauri/tauri.conf.json": _tauri_conf_version(),
        "desktop/package.json": _package_json_version(),
        "desktop/src-tauri/Cargo.toml": _cargo_toml_version(),
        "desktop_pdf_translator.__version__": _package_dunder_version(),
    }
    assert len(set(versions.values())) == 1, f"Version drift: {versions}"
