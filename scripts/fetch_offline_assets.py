#!/usr/bin/env python3
"""Stage the translation-engine assets the installer ships, into `assets/`.

Cross-platform successor to the Python that `fetch-offline-assets.ps1` used to
carry inline as a here-string (#69). The two thin launchers at the repo root —
`fetch-offline-assets.ps1` and `fetch-offline-assets.sh` — only find an
interpreter and hand over to this file.

PDFusion needs ~290 MB that is not part of any wheel before it can translate:
BabelDOC's layout models, embedding fonts and cmaps, and the Argos en->vi
language pack. Left unstaged, the app downloads them on first use — which is
what issue #21 is about. This drops both where `pdfusion-sidecar.spec` looks:

    assets/babeldoc/offline_assets_<tag>.zip
    assets/argos/translate-en_vi.argosmodel

Both are gitignored. Both are optional — the spec prints a WARN and the app
falls back to downloading at runtime if either is missing — so this is a step
you run once before building an installer, not a build dependency. It is
deliberately NOT wired into the build script or Tauri's `beforeBundleCommand`:
it needs the network and takes minutes, and a bundle step that silently
downloads a third of a gigabyte is the problem, not the fix.

`<tag>` is a hash of BabelDOC's own asset manifest, so the zip is only valid for
the babeldoc version it was built against. Re-run this after bumping babeldoc; a
stale zip is ignored at runtime (the app downloads instead), and this deletes it
when it stages a new one.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BABELDOC_DIR = REPO_ROOT / "assets" / "babeldoc"
ARGOS_DIR = REPO_ROOT / "assets" / "argos"

ARGOS_PACK = ARGOS_DIR / "translate-en_vi.argosmodel"


def stage_babeldoc(force: bool) -> None:
    # The _async variant, never the sync wrapper: babeldoc runs the sync one
    # through run_in_another_thread, where threading.excepthook swallows the
    # SystemExit its downloaders raise -- a failed build would look like a
    # successful one.
    from babeldoc.assets.assets import (
        generate_offline_assets_package_async,
        get_offline_assets_tag,
    )

    target = BABELDOC_DIR / f"offline_assets_{get_offline_assets_tag()}.zip"
    if target.exists() and not force:
        print(f"[skip]     {target.name} already staged")
        return

    BABELDOC_DIR.mkdir(parents=True, exist_ok=True)
    for stale in BABELDOC_DIR.glob("offline_assets_*.zip"):
        if stale != target:
            stale.unlink()
            print(f"[clean]    removed stale {stale.name}")

    print("[babeldoc] downloading assets and packaging (~210 MB, several minutes)")
    asyncio.run(generate_offline_assets_package_async(BABELDOC_DIR))
    if not target.exists():
        raise SystemExit(f"expected {target} to exist after packaging")
    size_mb = target.stat().st_size / 1024 / 1024
    print(f"[babeldoc] staged {target.name} ({size_mb:.0f} MB)")


def stage_argos(force: bool) -> None:
    import argostranslate.package as argos_package

    if ARGOS_PACK.exists() and not force:
        print(f"[skip]     {ARGOS_PACK.name} already staged")
        return

    print("[argos]    downloading en->vi language pack (~80 MB)")
    argos_package.update_package_index()
    entry = next(
        (
            p
            for p in argos_package.get_available_packages()
            if p.from_code == "en" and p.to_code == "vi"
        ),
        None,
    )
    if entry is None:
        raise SystemExit("Argos package index has no en->vi entry")
    ARGOS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(entry.download()), ARGOS_PACK)
    size_mb = ARGOS_PACK.stat().st_size / 1024 / 1024
    print(f"[argos]    staged {ARGOS_PACK.name} ({size_mb:.0f} MB)")


def repack_argos_sbd() -> None:
    # Upstream ships a stanza tokenizer inside the pack, and stanza loads torch
    # checkpoints -- that one 0.63 MB file is what puts torch (466 MB) and
    # transformers (119 MB) on the offline translate path. argostranslate reads
    # sentence boundaries from a bundled `minisbd/*.onnx` when one is present,
    # so swap the two: MiniSBD's en model is 0.19 MB of onnxruntime. Idempotent
    # -- a pack already carrying minisbd and no stanza is left alone.
    if not ARGOS_PACK.exists():
        return

    from minisbd.models import get_model_file

    with zipfile.ZipFile(ARGOS_PACK) as zf:
        names = zf.namelist()
    if not names:
        raise SystemExit(f"{ARGOS_PACK.name} is empty")
    prefix = names[0].split("/")[0]
    stanza_entries = [n for n in names if n.startswith(f"{prefix}/stanza/")]
    has_minisbd = any(
        n.startswith(f"{prefix}/minisbd/") and n.endswith(".onnx") for n in names
    )
    if has_minisbd and not stanza_entries:
        print(f"[argos]    {ARGOS_PACK.name} already uses MiniSBD sentence splitting")
        return

    sbd_model = Path(get_model_file("en"))
    before_mb = ARGOS_PACK.stat().st_size / 1024 / 1024
    # Sibling staging file + os.replace, so an interrupted repack never leaves a
    # half-written pack where a good one was.
    staging = ARGOS_PACK.with_name(ARGOS_PACK.name + ".repack")
    with zipfile.ZipFile(ARGOS_PACK) as src, zipfile.ZipFile(
        staging, "w", zipfile.ZIP_DEFLATED
    ) as dst:
        for item in src.infolist():
            if item.filename.startswith(f"{prefix}/stanza/"):
                continue
            dst.writestr(item, src.read(item.filename))
        dst.write(sbd_model, f"{prefix}/minisbd/{sbd_model.name}")
    os.replace(staging, ARGOS_PACK)

    after_mb = ARGOS_PACK.stat().st_size / 1024 / 1024
    print(
        f"[argos]    repacked {ARGOS_PACK.name}: dropped {len(stanza_entries)} stanza "
        f"entries, added minisbd/{sbd_model.name} "
        f"({before_mb:.1f} -> {after_mb:.1f} MB)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch even when the asset is already staged.",
    )
    args = parser.parse_args(argv)

    print(f"==> Staging offline engine assets with {sys.executable}", flush=True)
    stage_babeldoc(args.force)
    stage_argos(args.force)
    repack_argos_sbd()
    print("==> Done. Run the build-sidecar script to bundle them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
