#!/usr/bin/env python3
"""Build the PDFusion Python sidecar with PyInstaller and stage it for Tauri.

Cross-platform successor to `build-sidecar.ps1`'s body (#69). The two thin
launchers at the repo root — `build-sidecar.ps1` and `build-sidecar.sh` — exist
only to find an interpreter and hand over to this file; everything that decides
*what* gets built lives here so the platforms cannot drift.

Run it from a shell with the `pdfusion` environment active, or with
`PDFUSION_PYTHON` naming that environment's interpreter.

    python scripts/build_sidecar.py            # the real PyInstaller build
    python scripts/build_sidecar.py --stub     # placeholders, in seconds

Where the output is staged differs by platform, and the reason is PyInstaller's
one-dir bootloader: it resolves `_internal/` **relative to the executable**, so
the two have to stay siblings wherever the installer puts them.

**Windows.** The exe ships through Tauri's `externalBin`, which wants the rustc
host triple on the source filename and renames it at bundle time; `_internal/`
ships separately as a `resources` glob. Tauri puts `externalBin` at the install
root and preserves a resource's path from `src-tauri/`, so staging them as
`binaries/pdfusion-sidecar-<triple>.exe` and `src-tauri/_internal/` is what
lands them side by side.

**Linux and macOS.** `externalBin` goes to `/usr/bin` (deb, AppImage) or
`Contents/MacOS` (.app) while resources go to `/usr/lib/<product>` /
`Contents/Resources`. Two different directories, so that arrangement would
split the pair. The whole one-dir tree is staged as `src-tauri/sidecar/`
instead and shipped as one resource directory; `sidecar.rs`'s
`BUNDLED_SIDECAR_FILENAME` is the other half of this.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "pdfusion-sidecar.spec"
DIST_DIR = REPO_ROOT / "dist" / "pdfusion-sidecar"
SRC_TAURI = REPO_ROOT / "desktop" / "src-tauri"

IS_WINDOWS = sys.platform == "win32"
EXE_NAME = "pdfusion-sidecar.exe" if IS_WINDOWS else "pdfusion-sidecar"

#: Used only on Windows, where `externalBin` requires it on the source file.
DEFAULT_TRIPLE = "x86_64-pc-windows-msvc"


def log(message: str) -> None:
    print(f"==> {message}", flush=True)


def host_triple() -> str:
    """The rustc host triple, asked of rustc rather than assumed.

    Falls back to the x64 MSVC triple: this is only consulted on Windows, and
    a box building a Windows installer without rustc on PATH cannot get as far
    as the cargo build anyway — a wrong guess here would be noticed
    immediately, where a hard failure would break `--stub` on a machine that
    has not installed Rust yet.
    """
    try:
        out = subprocess.run(
            ["rustc", "-vV"], capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return DEFAULT_TRIPLE
    for line in out.splitlines():
        if line.startswith("host:"):
            return line.split(":", 1)[1].strip()
    return DEFAULT_TRIPLE


def staged_paths(triple: str) -> tuple[Path, Path]:
    """`(executable, internals)` as they are staged for this platform."""
    if IS_WINDOWS:
        return (
            SRC_TAURI / "binaries" / f"pdfusion-sidecar-{triple}.exe",
            SRC_TAURI / "_internal",
        )
    tree = SRC_TAURI / "sidecar"
    return tree / EXE_NAME, tree / "_internal"


def write_stubs(triple: str) -> None:
    """Placeholders that satisfy Tauri's compile-time validation of
    `externalBin` and `resources` without a ~15-minute PyInstaller run.

    `sidecar.rs:check_staged_size` rejects anything under 1 MiB, so the runtime
    falls through to the local interpreter and `pnpm tauri dev` behaves exactly
    as it does with nothing staged at all. Never ship a stubbed installer: the
    bundled sidecar is zero bytes.
    """
    exe, internals = staged_paths(triple)
    log("Writing stub binaries (no PyInstaller)")
    exe.parent.mkdir(parents=True, exist_ok=True)
    if not exe.exists():
        exe.touch()
    internals.mkdir(parents=True, exist_ok=True)
    marker = internals / ".placeholder"
    if not marker.exists():
        marker.touch()
    log(f"Stubs ready at {exe.parent}")
    print("    Cargo / Tauri build steps will compile, but the bundled sidecar")
    print("    will not launch. Run without --stub to produce the real build.")


def pyinstaller_command() -> list[str]:
    """How to invoke PyInstaller from here.

    `pyinstaller` on PATH is the happy path, but it is not reachable on the one
    path that matters most: Tauri's `beforeBundleCommand` spawns a bare shell
    that does not inherit a conda-activated PATH, so `pnpm tauri build` — the
    documented way to build the installer — always arrived with the env's
    scripts directory missing. `sys.executable` is this very interpreter, which
    the launcher already resolved through `PDFUSION_PYTHON` or an active env,
    so `-m PyInstaller` is the reliable form and the PATH lookup is only a
    nicety.
    """
    found = shutil.which("pyinstaller")
    if found:
        return [found]
    log("pyinstaller not on PATH; using this interpreter's -m PyInstaller")
    return [sys.executable, "-m", "PyInstaller"]


def run_pyinstaller() -> None:
    if not SPEC_PATH.exists():
        raise SystemExit(f"Spec file not found at {SPEC_PATH}")

    # --distpath/--workpath are not optional niceties: PyInstaller defaults both
    # to paths relative to the *current directory*, and Tauri runs
    # beforeBundleCommand from desktop/. Without them the one-dir tree lands in
    # desktop/dist/ — which is `frontendDist`, so a ~1 GB copy of the Python
    # runtime would be bundled into the installer as frontend assets — while
    # DIST_DIR below still points at the repo root and this throws.
    cmd = [
        *pyinstaller_command(),
        str(SPEC_PATH),
        "--clean",
        "--noconfirm",
        "--distpath",
        str(REPO_ROOT / "dist"),
        "--workpath",
        str(REPO_ROOT / "build"),
    ]
    log("Building PDFusion sidecar with PyInstaller")
    subprocess.run(cmd, check=True, cwd=REPO_ROOT)

    if not (DIST_DIR / EXE_NAME).exists():
        raise SystemExit(f"PyInstaller output missing: {DIST_DIR / EXE_NAME}")


def strip_shared_libraries(tree: Path) -> None:
    """Drop the symbol tables Linux wheels ship in their shared libraries.

    171 MB of the v1.2.0 Linux tree was symbols no one reads at runtime —
    60 MB in ctranslate2's extension alone, 17 MB in libpython. Windows wheels
    keep theirs in separate PDBs that are never collected, which is part of why
    the Linux tree was so much larger.

    `--strip-unneeded` is what Debian's own `dh_strip` applies to shared
    libraries: `.dynsym`, the table the dynamic loader and `dlopen` resolve
    against, is kept. Only `.so` files under `_internal/` are touched — never
    the executable, which is PyInstaller's bootloader with the Python archive
    appended to it and would lose that archive. Not PyInstaller's `strip=True`
    either: that strips the bootloader too.

    Linux only. On macOS stripping invalidates the ad-hoc signature every
    arm64 dylib carries, and the loader then refuses it.
    """
    strip = shutil.which("strip")
    if strip is None:
        log("WARN: `strip` not found (binutils); shipping unstripped libraries")
        return
    libs = [
        p
        for p in (tree / "_internal").rglob("*.so*")
        if p.is_file() and not p.is_symlink()
    ]
    before = sum(p.stat().st_size for p in libs)
    # Batched: one process per file is ~1,000 spawns.
    for i in range(0, len(libs), 200):
        subprocess.run(
            [strip, "--strip-unneeded", *map(str, libs[i : i + 200])], check=True
        )
    after = sum(p.stat().st_size for p in libs)
    log(f"Stripped {len(libs)} shared libraries: {(before - after) / 1e6:.0f} MB saved")


def warn_on_gui_opencv(tree: Path) -> None:
    """Say so when the bundle carries opencv's Qt build instead of the headless one.

    BabelDOC asks for `opencv-python-headless`, but its `rapidocr-onnxruntime`
    dependency asks for `opencv-python`, and both install into the same `cv2/`
    — whichever pip wrote last wins. On Linux the GUI wheel's `cv2` links Qt5
    and its own ffmpeg from `opencv_python.libs/`, 121 MB that a sidecar with
    no window never uses. The release and CI workflows reinstall the headless
    wheel before building; a local build may not have.
    """
    if (tree / "_internal" / "opencv_python.libs").is_dir():
        log(
            "WARN: cv2 is opencv-python's Qt build (+121 MB). To ship the "
            "headless one: pip uninstall -y opencv-python && pip install "
            "--force-reinstall --no-deps opencv-python-headless==<same version>"
        )


def stage(triple: str) -> None:
    exe, internals = staged_paths(triple)
    log(f"Staging into {exe.parent}")

    if IS_WINDOWS:
        # Two separate destinations; clear each one before copying so a
        # previous build's files can never survive into this one.
        exe.unlink(missing_ok=True)
        shutil.rmtree(internals, ignore_errors=True)
        exe.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DIST_DIR / EXE_NAME, exe)
        shutil.copytree(DIST_DIR / "_internal", internals)
    else:
        # One tree, so the executable and its `_internal/` stay siblings.
        tree = exe.parent
        shutil.rmtree(tree, ignore_errors=True)
        shutil.copytree(DIST_DIR, tree, symlinks=True)
        # copytree preserves mode, but a umask-mangled or archive-restored
        # source can arrive without it, and a resource that is not executable
        # is a sidecar that cannot spawn.
        exe.chmod(exe.stat().st_mode | 0o755)

    log("Done.")
    print(f"    executable: {exe}")
    print(f"    internals:  {internals}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--stub",
        action="store_true",
        help=(
            "Skip PyInstaller and drop placeholders, so `cargo check` and "
            "`pnpm tauri dev` work on a fresh checkout in seconds."
        ),
    )
    parser.add_argument(
        "--triple",
        default=None,
        help=(
            "rustc host triple for the externalBin filename (Windows only; "
            "defaults to `rustc -vV`'s answer)."
        ),
    )
    args = parser.parse_args(argv)

    triple = args.triple or host_triple()
    if args.stub:
        write_stubs(triple)
        return 0

    run_pyinstaller()
    if sys.platform.startswith("linux"):
        # Before staging, so `dist/` — which the smoke suite also runs — is the
        # tree that ships.
        strip_shared_libraries(DIST_DIR)
        warn_on_gui_opencv(DIST_DIR)
    stage(triple)
    return 0


if __name__ == "__main__":
    # `PYTHONUTF8` so the box-drawing and arrows PyInstaller prints survive a
    # Windows console still on a legacy code page.
    os.environ.setdefault("PYTHONUTF8", "1")
    sys.exit(main())
