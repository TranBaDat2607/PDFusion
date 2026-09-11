"""`utils/paths.py`: where the app keeps its data (#59).

The Tauri shell resolves `%LOCALAPPDATA%\\PDFusion` (`sidecar.rs:appdata_dir`),
pre-creates the layout there and runs the sidecar in it. Python hardcoded
`~/AppData/Local/PDFusion` instead — a different folder wherever Local AppData
has been relocated. Every test points both environment variables at `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

import pytest

from desktop_pdf_translator.utils.paths import adopt_legacy_config, appdata_dir


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def relocated(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> Tuple[Path, Path]:
    """A machine whose Local AppData is not under the home folder, so the
    legacy root and the real root are two different folders."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "relocated"))
    legacy = home / "AppData" / "Local" / "PDFusion"
    legacy.mkdir(parents=True)
    return legacy, appdata_dir()


def test_the_root_follows_localappdata(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "relocated"))

    root = appdata_dir()

    assert root == tmp_path / "relocated" / "PDFusion"
    assert root.is_dir()


def test_without_localappdata_the_root_is_under_the_home_folder(
    home: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    assert appdata_dir() == home / "AppData" / "Local" / "PDFusion"


def test_a_config_left_at_the_legacy_root_is_adopted(relocated: Tuple[Path, Path]):
    """Otherwise the first start after the move finds no config and runs on
    defaults — API keys included. The keys are DPAPI-protected for the user,
    not for a path, so the copy still decrypts."""
    legacy, root = relocated
    (legacy / "config.toml").write_text("[openai]\napi_key = \"dpapi:AAAA\"\n", encoding="utf-8")
    (legacy / "config.toml.bak").write_text("[openai]\n", encoding="utf-8")

    assert adopt_legacy_config(root) is True

    assert (root / "config.toml").read_bytes() == (legacy / "config.toml").read_bytes()
    assert (root / "config.toml.bak").read_bytes() == (legacy / "config.toml.bak").read_bytes()
    # Copied, not moved.
    assert (legacy / "config.toml").exists()


def test_a_config_already_at_the_root_is_never_overwritten(relocated: Tuple[Path, Path]):
    legacy, root = relocated
    (legacy / "config.toml").write_text("legacy", encoding="utf-8")
    (root / "config.toml").write_text("current", encoding="utf-8")

    assert adopt_legacy_config(root) is False

    assert (root / "config.toml").read_text(encoding="utf-8") == "current"


def test_nothing_is_adopted_without_a_legacy_config(relocated: Tuple[Path, Path]):
    _, root = relocated

    assert adopt_legacy_config(root) is False

    assert not (root / "config.toml").exists()
