#!/usr/bin/env python3
"""Shrink the `.deb` Tauri produces, without changing how the app behaves.

    python scripts/repack_deb.py                       # every .deb under target/release/bundle/deb
    python scripts/repack_deb.py path/to/PDFusion.deb  # just this one

Three things in what tauri-bundler writes, none configurable from
`tauri.linux.conf.json`. Measured on the v1.2.0 payload after the build-side
cuts (stripping, headless OpenCV): 697 MB → 436 MB.

**It dereferences symlinks.** On Linux PyInstaller lays out a one-dir build
with symlinks: a vendored library lives once, in its wheel's `<pkg>.libs/`
directory, and `_internal/` carries a link to it. `build_sidecar.py:stage`
copies the tree with `symlinks=True`, but the bundler follows every link when
it copies `resources`, so v1.2.0's `.deb` carried 42 libraries twice — 296 MB
of byte-identical copies (libctranslate2, OpenBLAS twice, libmupdf, opencv's
ffmpeg, …). Put back as symlinks, `dpkg` installs the layout PyInstaller built.

**It compresses with gzip.** gzip's 32 KB window cannot see the duplicates
above, nor much of the redundancy across ~1 GB of shared libraries. xz is
what Debian itself ships, and every dpkg in support reads it (zstd would
need dpkg ≥ 1.21.18, i.e. Debian 12 / Ubuntu 21.10, for less gain). The cost
is at install time: xz unpacks this payload in roughly ten seconds, which is
what the NSIS installer's LZMA already costs on Windows.

**The engine-assets zip is already deflated**, which xz cannot improve on —
see `store_asset_zips`.

Every other regular file keeps its bytes and mode. The control file keeps
every field except `Installed-Size`, recomputed for the links and the larger
zip; `md5sums` is regenerated, since Tauri's lists files that are now links.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEB_DIR = REPO_ROOT / "desktop" / "src-tauri" / "target" / "release" / "bundle" / "deb"

#: Under this, a duplicate is not worth a symlink. Every duplicate in v1.2.0
#: was a shared library of 0.2 MB or more; the floor just keeps the hashing
#: pass from reading ~20k small .py/.pyc files for nothing.
MIN_DEDUPE_BYTES = 64 * 1024


def log(message: str) -> None:
    print(f"==> {message}", flush=True)


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _canonical(paths: list[Path]) -> Path:
    """Which copy of a duplicate stays a real file.

    The deepest one — the copy in `<pkg>.libs/` rather than the one at the top
    of `_internal/` — which is the direction PyInstaller itself links in: the
    file stays where its wheel put it, next to the extension whose RUNPATH
    names it, and the top level holds the pointer.
    """
    return max(paths, key=lambda p: (len(p.parts), str(p)))


def relink_duplicates(root: Path) -> int:
    """Replace byte-identical copies under `root` with relative symlinks.

    Returns the bytes saved. Grouped by size first so only candidates are
    hashed; the hash, not the name, is what decides, so nothing that merely
    shares a filename is ever linked.
    """
    by_size: dict[int, list[Path]] = defaultdict(list)
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            size = path.stat().st_size
            if size >= MIN_DEDUPE_BYTES:
                by_size[size].append(path)

    saved = 0
    for size, candidates in by_size.items():
        if len(candidates) < 2:
            continue
        by_hash: dict[str, list[Path]] = defaultdict(list)
        for path in candidates:
            by_hash[_digest(path)].append(path)
        for group in by_hash.values():
            if len(group) < 2:
                continue
            keep = _canonical(group)
            for dup in group:
                if dup == keep:
                    continue
                dup.unlink()
                dup.symlink_to(os.path.relpath(keep, dup.parent))
                saved += size
    return saved


def store_asset_zips(root: Path) -> int:
    """Rewrite BabelDOC's offline-assets zip without compression.

    The zip is 217 MB deflated, and xz cannot do anything more with deflate
    output: it passed through at 227 MB. Stored, the 357 MB of fonts and
    models underneath compress to 156 MB inside the `.deb` — 71 MB less to
    download, for 140 MB more on disk under `/usr/lib`. Restoring it is
    `restore_offline_assets_package_async`, which verifies every member's
    sha3_256 and never the archive's own bytes or compression, and the
    filename (the manifest hash it globs for) is unchanged.

    Here rather than in fetch_offline_assets because NSIS's solid LZMA would
    trade the same way, and that choice is the Windows installer's to make.

    Returns the change in bytes on disk.
    """
    grown = 0
    for zpath in root.rglob("babeldoc_assets/offline_assets_*.zip"):
        before = zpath.stat().st_size
        tmp = zpath.with_suffix(".stored")
        with zipfile.ZipFile(zpath) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as dst:
            for info in src.infolist():
                with src.open(info) as fin, dst.open(info.filename, "w") as fout:
                    shutil.copyfileobj(fin, fout, 1 << 20)
        shutil.copymode(zpath, tmp)
        tmp.replace(zpath)
        grown += zpath.stat().st_size - before
    return grown


def installed_size_kib(root: Path) -> int:
    """What `Installed-Size` means per Debian policy: KiB, links counted as 1."""
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        if Path(dirpath).name == "DEBIAN" and Path(dirpath).parent == root:
            continue
        for name in files:
            path = Path(dirpath) / name
            total += 1 if path.is_symlink() else path.stat().st_size
    return (total + 1023) // 1024


def rewrite_control(pkg: Path) -> None:
    control = pkg / "DEBIAN" / "control"
    size = installed_size_kib(pkg)
    lines = control.read_text(encoding="utf-8").splitlines()
    lines = [f"Installed-Size: {size}" if ln.startswith("Installed-Size:") else ln for ln in lines]
    control.write_text("\n".join(lines) + "\n", encoding="utf-8")

    sums = []
    for dirpath, dirs, files in os.walk(pkg):
        dirs.sort()
        if Path(dirpath) == pkg:
            dirs[:] = [d for d in dirs if d != "DEBIAN"]
        for name in sorted(files):
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            md5 = hashlib.md5(path.read_bytes()).hexdigest()
            sums.append(f"{md5}  {path.relative_to(pkg).as_posix()}")
    (pkg / "DEBIAN" / "md5sums").write_text("\n".join(sums) + "\n", encoding="utf-8")


def repack(deb: Path) -> None:
    before = deb.stat().st_size
    log(f"Repacking {deb.name} ({before / 1e6:.1f} MB)")
    with tempfile.TemporaryDirectory(prefix="repack-deb-", dir=deb.parent) as tmp:
        pkg = Path(tmp) / "pkg"
        # -R keeps DEBIAN/ alongside the payload, so the control file, and any
        # maintainer script a future Tauri adds, survive untouched.
        subprocess.run(["dpkg-deb", "-R", str(deb), str(pkg)], check=True)

        saved = relink_duplicates(pkg)
        log(f"Relinked duplicate files: {saved / 1e6:.1f} MB")
        grown = store_asset_zips(pkg)
        log(f"Stored the engine-assets zip uncompressed: +{grown / 1e6:.1f} MB on disk")
        rewrite_control(pkg)

        out = Path(tmp) / deb.name
        # --root-owner-group because the extraction above ran unprivileged:
        # without it every file would be owned by the CI runner's uid.
        subprocess.run(
            ["dpkg-deb", "--root-owner-group", "-Zxz", "-z9", "-b", str(pkg), str(out)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        shutil.move(str(out), deb)
    after = deb.stat().st_size
    log(f"{deb.name}: {before / 1e6:.1f} MB → {after / 1e6:.1f} MB")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("debs", nargs="*", type=Path, help=f"defaults to every .deb in {DEB_DIR}")
    args = parser.parse_args(argv)

    if shutil.which("dpkg-deb") is None:
        raise SystemExit("dpkg-deb not found; this runs on the Debian/Ubuntu box that built the .deb")

    debs = args.debs or sorted(DEB_DIR.glob("*.deb"))
    if not debs:
        raise SystemExit(f"No .deb to repack in {DEB_DIR}")
    for deb in debs:
        repack(deb.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
