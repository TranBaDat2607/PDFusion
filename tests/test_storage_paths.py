"""`utils/paths.py`: where the app keeps its data (#59, #69).

Two rules are under test. The first is that the Tauri shell and the sidecar
resolve the *same* root — the shell pre-creates the layout there and makes it
the sidecar's cwd, so a disagreement means Python writes somewhere the shell
never prepared. `sidecar.rs` exports `PDFUSION_DATA_DIR` for exactly that
reason, and it has to win over every platform default.

The second is that the default, when nothing exported anything, is the
platform's own convention rather than the literal `~/AppData/Local/PDFusion`
Python used to hardcode — a folder that is wrong on Windows wherever Local
AppData has been relocated, and wrong by construction on Linux and macOS.

Every test drives the resolution through the environment, never through the
real one: `conftest.py` has already pointed the whole run at a temp root.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Tuple

import pytest

from desktop_pdf_translator.utils import paths
from desktop_pdf_translator.utils.paths import (
    DATA_DIR_ENV,
    adopt_legacy_config,
    appdata_dir,
)

on_windows = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows resolves its root through %LOCALAPPDATA%"
)
on_macos = pytest.mark.skipif(
    sys.platform != "darwin", reason="macOS keeps data in ~/Library/Application Support"
)
on_linux = pytest.mark.skipif(
    sys.platform in ("win32", "darwin"), reason="XDG is the Linux/BSD convention"
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway home, with the shell's override out of the way so the
    platform rules are what answers."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    return home


@pytest.fixture
def relocated(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> Tuple[Path, Path]:
    """A root that is not the legacy one, so the two are distinguishable.

    On Windows that is a machine whose Local AppData has been moved off the
    home folder; everywhere else it is simply the platform convention, which is
    never `~/AppData/Local`.
    """
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "relocated" / "PDFusion"))
    legacy = home / "AppData" / "Local" / "PDFusion"
    legacy.mkdir(parents=True)
    return legacy, appdata_dir()


def test_the_shells_answer_wins_over_every_platform_default(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
):
    """`sidecar.rs` exports this on both spawn paths. If Python preferred its
    own answer the two would agree only by coincidence."""
    exported = tmp_path / "wherever-the-shell-said"
    monkeypatch.setenv(DATA_DIR_ENV, str(exported))

    root = appdata_dir()

    assert root == exported
    assert root.is_dir()


def test_an_empty_override_is_not_an_answer(
    home: Path, monkeypatch: pytest.MonkeyPatch
):
    """An exported-but-empty variable is how a shell says nothing, not how it
    names the filesystem root."""
    monkeypatch.setenv(DATA_DIR_ENV, "")

    # Not the cwd, and not the filesystem root: the platform default.
    assert appdata_dir().is_relative_to(home)


@on_windows
def test_on_windows_the_root_follows_localappdata(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "relocated"))

    assert appdata_dir() == tmp_path / "relocated" / "PDFusion"


@on_windows
def test_on_windows_without_localappdata_the_root_is_under_the_home_folder(
    home: Path,
):
    assert appdata_dir() == home / "AppData" / "Local" / "PDFusion"


@on_macos
def test_on_macos_the_root_is_application_support(home: Path):
    assert appdata_dir() == home / "Library" / "Application Support" / "PDFusion"


@on_linux
def test_on_linux_the_root_follows_xdg_data_home(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    assert appdata_dir() == tmp_path / "xdg" / "PDFusion"


@on_linux
def test_on_linux_without_xdg_the_root_is_local_share(home: Path):
    assert appdata_dir() == home / ".local" / "share" / "PDFusion"


@on_linux
def test_on_linux_a_relative_xdg_data_home_is_ignored(
    home: Path, monkeypatch: pytest.MonkeyPatch
):
    """The spec says so, and it matters here specifically: the shell makes the
    data root the sidecar's cwd, so a relative value would resolve against the
    directory it is supposed to be choosing."""
    monkeypatch.setenv("XDG_DATA_HOME", "relative/share")

    assert appdata_dir() == home / ".local" / "share" / "PDFusion"


@on_linux
def test_on_linux_the_legacy_appdata_folder_is_not_a_fallback(home: Path):
    """It is nobody's convention off Windows. A config left there is carried
    across once by `adopt_legacy_config`, not written to forever."""
    legacy = home / "AppData" / "Local" / "PDFusion"
    legacy.mkdir(parents=True)

    assert appdata_dir() != legacy


# ---------------------------------------------------------------------------
# no home directory at all
# ---------------------------------------------------------------------------


@pytest.fixture
def no_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """`Path.home()` raising, as it does where neither `HOME` nor a passwd
    entry resolves. The environment variables are gone too, since `Path.home()`
    is what reads them."""

    def raise_no_home() -> Path:
        raise RuntimeError("Could not determine home directory")

    monkeypatch.delenv("USERPROFILE", raising=False)
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(raise_no_home))


def test_a_candidate_that_needs_no_home_survives_one_that_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_home: None
):
    """A candidate that needs a home costs only itself.

    The regression, and the one place it bites: on Windows the candidates were
    built in a single expression, so the legacy home-based path raising
    discarded the `%LOCALAPPDATA%` entry *beside* it and the root became the
    temp dir — volatile, so config, keys and caches were lost on reboot. The
    shell never had this; `data_dir_candidates` pushes `local_appdata`
    independently of an `Option`-returning `home_dir()`.

    Driven through `sys.platform` rather than a marker so it runs on every
    runner: it is the Windows branch that regressed, and the `%LOCALAPPDATA%`
    candidate is a plain path with nothing platform-specific about resolving
    it. The end-to-end Windows case is below.
    """
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    assert paths._platform_data_dirs() == [tmp_path / "local" / "PDFusion"]


@on_windows
def test_without_a_home_the_root_still_follows_localappdata(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, no_home: None
):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    assert appdata_dir() == tmp_path / "local" / "PDFusion"


@on_linux
def test_without_a_home_the_root_still_follows_xdg_data_home(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, no_home: None
):
    """The mirror of the case above, and an invariant rather than a regression
    guard: `XDG_DATA_HOME` never needed a home to begin with, so this is what
    stops one being resolved eagerly here later."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    assert appdata_dir() == tmp_path / "xdg" / "PDFusion"


def test_the_shells_answer_needs_no_home_either(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, no_home: None
):
    """The override is what makes the two resolvers provably agree, so it has
    to survive the case where this platform's own rules cannot answer."""
    exported = tmp_path / "exported" / "PDFusion"
    monkeypatch.setenv(DATA_DIR_ENV, str(exported))

    assert appdata_dir() == exported


def test_without_a_home_there_is_nothing_to_adopt(tmp_path: Path, no_home: None):
    """`adopt_legacy_config` names a home-based folder, so with no home there
    is no source — and reporting that is not the same as raising out of
    `ConfigManager.__init__`, which runs on the sidecar's boot path."""
    root = tmp_path / "root"
    root.mkdir()

    assert adopt_legacy_config(root) is False


def test_a_config_left_at_the_legacy_root_is_adopted(relocated: Tuple[Path, Path]):
    """Otherwise the first start after the move finds no config and runs on
    defaults — API keys included. On Windows the keys are DPAPI-protected for
    the user, not for a path; off Windows they carry their own salt. Either
    way the copy still decrypts."""
    legacy, root = relocated
    (legacy / "config.toml").write_text("[openai]\napi_key = \"dpapi:AAAA\"\n", encoding="utf-8")
    (legacy / "config.toml.bak").write_text("[openai]\n", encoding="utf-8")

    assert adopt_legacy_config(root) is True

    assert (root / "config.toml").read_bytes() == (legacy / "config.toml").read_bytes()
    assert (root / "config.toml.bak").read_bytes() == (legacy / "config.toml.bak").read_bytes()
    # Copied, not moved.
    assert (legacy / "config.toml").exists()


def test_the_records_database_is_left_where_it_is(relocated: Tuple[Path, Path]):
    """`pdfusion.db` is a live SQLite database with its own WAL; a byte copy of
    one is how a records store gets corrupted. The log names its old location
    instead."""
    legacy, root = relocated
    (legacy / "config.toml").write_text("", encoding="utf-8")
    (legacy / "pdfusion.db").write_bytes(b"SQLite format 3\x00")

    assert adopt_legacy_config(root) is True

    assert not (root / "pdfusion.db").exists()


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
