"""Packaging: where the sidecar is staged, and where each bundler looks (#69).

Three files have to agree about one arrangement, and none of them imports the
others:

* `scripts/build_sidecar.py` decides where the PyInstaller output is *staged*,
* `desktop/src-tauri/tauri.<platform>.conf.json` decides what is *shipped*,
* `desktop/src-tauri/src/sidecar.rs` decides where the shipped copy is *found*.

Get any pair out of step and the symptom is identical on every platform — the
shell logs "No bundled sidecar at …", falls through to the dev interpreter, and
a *shipped* install silently depends on the user having a conda env. So the
agreement is asserted here rather than left to three comments.

The Rust constant is read as text: this suite has no Rust toolchain, and the
line it matches is a `const` declaration, not something that could drift into
meaning something else while still matching.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SRC_TAURI = _ROOT / "desktop" / "src-tauri"


def _load_build_sidecar():
    """`scripts/build_sidecar.py`, loaded by path.

    `scripts/` is a directory of runnable files, not a package — nothing
    imports it, the launchers hand it to an interpreter by path — so there is
    no `__init__.py` to make `import scripts.build_sidecar` work, and adding
    one would only exist to serve this test.
    """
    spec = importlib.util.spec_from_file_location(
        "pdfusion_build_sidecar", _ROOT / "scripts" / "build_sidecar.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_sidecar = _load_build_sidecar()

PLATFORMS = ("windows", "linux", "macos")


def platform_config(name: str) -> dict:
    return json.loads((_SRC_TAURI / f"tauri.{name}.conf.json").read_text(encoding="utf-8"))


def shared_config() -> dict:
    return json.loads((_SRC_TAURI / "tauri.conf.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", PLATFORMS)
def test_every_platform_declares_its_own_packaging(name: str):
    """Tauri merges these over the shared config for the target being built."""
    config = platform_config(name)

    assert config["bundle"]["targets"], f"{name} names no bundle target"
    assert config["build"]["beforeBundleCommand"], f"{name} builds no sidecar"


def test_the_shared_config_declares_none_of_it():
    """`bundle.targets` cannot name a target the host cannot build, and
    `beforeBundleCommand` is a shell command — so a value here would be wrong
    on at least two of the three platforms rather than a useful default."""
    config = shared_config()

    assert "targets" not in config["bundle"]
    assert "externalBin" not in config["bundle"]
    assert "resources" not in config["bundle"]
    assert "beforeBundleCommand" not in config["build"]


def test_only_windows_ships_the_sidecar_as_an_external_binary():
    """Off Windows `externalBin` lands in `/usr/bin` or `Contents/MacOS` while
    resources land elsewhere, which splits the executable from the `_internal/`
    tree PyInstaller's one-dir bootloader resolves relative to it."""
    assert platform_config("windows")["bundle"]["externalBin"] == [
        "binaries/pdfusion-sidecar"
    ]
    for name in ("linux", "macos"):
        assert platform_config(name)["bundle"]["externalBin"] == [], name


@pytest.mark.parametrize("name", ("linux", "macos"))
def test_the_posix_bundlers_ship_the_whole_one_dir_tree(name: str):
    assert "sidecar/**/*" in platform_config(name)["bundle"]["resources"]


def test_windows_ships_the_internals_beside_the_renamed_exe():
    assert "_internal/**/*" in platform_config("windows")["bundle"]["resources"]


# ---------------------------------------------------------------------------
# The staging side, and the Rust constant that has to match it
# ---------------------------------------------------------------------------


def rust_bundled_filenames() -> dict[str, str]:
    """`BUNDLED_SIDECAR_FILENAME` per `cfg`, read out of `sidecar.rs`."""
    source = (_SRC_TAURI / "src" / "sidecar.rs").read_text(encoding="utf-8")
    matches = re.findall(
        r'#\[cfg\((not\(windows\)|windows)\)\]\s*\n'
        r'const BUNDLED_SIDECAR_FILENAME: &str = "([^"]+)";',
        source,
    )
    assert len(matches) == 2, f"expected two cfg'd constants, found {matches}"
    return dict(matches)


def staged_relative_to_src_tauri(monkeypatch: pytest.MonkeyPatch, windows: bool) -> Path:
    monkeypatch.setattr(build_sidecar, "IS_WINDOWS", windows)
    monkeypatch.setattr(
        build_sidecar, "EXE_NAME", "pdfusion-sidecar.exe" if windows else "pdfusion-sidecar"
    )
    exe, _internals = build_sidecar.staged_paths("x86_64-pc-windows-msvc")
    return exe.relative_to(_SRC_TAURI)


def test_the_posix_binary_is_staged_where_the_shell_looks_for_it(
    monkeypatch: pytest.MonkeyPatch,
):
    staged = staged_relative_to_src_tauri(monkeypatch, windows=False)

    assert staged.as_posix() == rust_bundled_filenames()["not(windows)"]


def test_the_posix_internals_stay_beside_the_binary(monkeypatch: pytest.MonkeyPatch):
    """The whole reason the POSIX layout is a directory rather than an
    `externalBin`: the bootloader resolves `_internal/` against the executable's
    own directory."""
    monkeypatch.setattr(build_sidecar, "IS_WINDOWS", False)
    monkeypatch.setattr(build_sidecar, "EXE_NAME", "pdfusion-sidecar")

    exe, internals = build_sidecar.staged_paths("irrelevant")

    assert internals.parent == exe.parent
    assert internals.name == "_internal"


def test_the_windows_exe_carries_the_triple_tauri_requires(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(build_sidecar, "IS_WINDOWS", True)
    monkeypatch.setattr(build_sidecar, "EXE_NAME", "pdfusion-sidecar.exe")

    exe, internals = build_sidecar.staged_paths("x86_64-pc-windows-msvc")

    assert exe.name == "pdfusion-sidecar-x86_64-pc-windows-msvc.exe"
    assert exe.parent.name == "binaries"
    # Not inside binaries/: the resources glob preserves its path from
    # src-tauri/, and it has to install next to the *renamed* exe at the root.
    assert internals == _SRC_TAURI / "_internal"


def test_the_staged_windows_binary_matches_the_shell_constant():
    """The exe Tauri renames at bundle time, stripped of its triple."""
    assert rust_bundled_filenames()["windows"] == "pdfusion-sidecar.exe"
