"""`scripts/repack_deb.py`: the duplicates it links, and the ones it must not.

The payload it rewrites is the Linux installer, and a wrong link there is a
library that silently resolves to a different file. So the rule under test is
narrow: only byte-identical files become links, and the copy that stays real is
the one inside its wheel's directory.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows; the script runs on Linux"
)


def _load_repack_deb():
    # By path, for the reason test_packaging_config gives: scripts/ is not a package.
    spec = importlib.util.spec_from_file_location("pdfusion_repack_deb", _ROOT / "scripts" / "repack_deb.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


repack_deb = _load_repack_deb()

BIG = repack_deb.MIN_DEDUPE_BYTES


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_a_top_level_copy_becomes_a_link_into_the_wheels_libs_dir(tmp_path: Path):
    lib = b"\x7fELF" + b"a" * BIG
    top = _write(tmp_path / "_internal" / "libfoo.so", lib)
    real = _write(tmp_path / "_internal" / "foo.libs" / "libfoo.so", lib)

    saved = repack_deb.relink_duplicates(tmp_path)

    assert saved == len(lib)
    assert top.is_symlink() and not real.is_symlink()
    assert os.readlink(top) == os.path.join("foo.libs", "libfoo.so")
    assert top.read_bytes() == lib


def test_files_that_only_share_a_name_or_a_size_are_left_alone(tmp_path: Path):
    a = _write(tmp_path / "_internal" / "libfoo.so", b"a" * BIG)
    b = _write(tmp_path / "_internal" / "foo.libs" / "libfoo.so", b"b" * BIG)

    assert repack_deb.relink_duplicates(tmp_path) == 0
    assert not a.is_symlink() and not b.is_symlink()


def test_small_duplicates_are_not_worth_a_link(tmp_path: Path):
    a = _write(tmp_path / "x" / "__init__.py", b"")
    b = _write(tmp_path / "y" / "__init__.py", b"")

    assert repack_deb.relink_duplicates(tmp_path) == 0
    assert not a.is_symlink() and not b.is_symlink()


def test_installed_size_counts_the_link_not_its_target(tmp_path: Path):
    _write(tmp_path / "DEBIAN" / "control", b"x" * 4096)
    real = _write(tmp_path / "usr" / "lib" / "a.so", b"a" * 10 * 1024)
    (tmp_path / "usr" / "lib" / "b.so").symlink_to(real.name)

    # 10 KiB of payload plus a one-byte link, rounded up; DEBIAN/ excluded.
    assert repack_deb.installed_size_kib(tmp_path) == 11


@pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="needs dpkg-deb")
def test_a_repacked_deb_installs_the_same_files(tmp_path: Path):
    pkg = tmp_path / "pkg"
    lib = b"\x7fELF" + os.urandom(BIG)
    _write(pkg / "usr" / "lib" / "app" / "_internal" / "libfoo.so", lib)
    _write(pkg / "usr" / "lib" / "app" / "_internal" / "foo.libs" / "libfoo.so", lib)
    _write(pkg / "usr" / "bin" / "app", b"#!/bin/sh\n").chmod(0o755)
    _write(
        pkg / "DEBIAN" / "control",
        b"Package: app\nVersion: 1.0\nArchitecture: amd64\nInstalled-Size: 999\n"
        b"Maintainer: x\nDescription: x\n",
    )
    deb = tmp_path / "app.deb"
    subprocess.run(
        ["dpkg-deb", "--root-owner-group", "-Zgzip", "-b", str(pkg), str(deb)],
        check=True,
        stdout=subprocess.DEVNULL,
    )

    repack_deb.repack(deb)

    out = tmp_path / "out"
    subprocess.run(["dpkg-deb", "-x", str(deb), str(out)], check=True)
    top = out / "usr" / "lib" / "app" / "_internal" / "libfoo.so"
    assert top.is_symlink() and top.read_bytes() == lib
    assert os.access(out / "usr" / "bin" / "app", os.X_OK)

    info = subprocess.run(["dpkg-deb", "-f", str(deb), "Installed-Size"], capture_output=True, text=True, check=True)
    assert int(info.stdout) < 999
    md5sums = subprocess.run(["dpkg-deb", "-I", str(deb), "md5sums"], capture_output=True, text=True, check=True)
    assert "foo.libs/libfoo.so" in md5sums.stdout
    assert "_internal/libfoo.so" not in md5sums.stdout
