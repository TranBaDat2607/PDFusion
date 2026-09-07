#requires -Version 5.1
<#
.SYNOPSIS
    Stage the translation-engine assets the installer ships, into assets/.

.DESCRIPTION
    PDFusion needs ~290 MB that is not part of any wheel before it can
    translate: BabelDOC's layout models, embedding fonts and cmaps, and the
    Argos en->vi language pack. Left unstaged, the app downloads them on first
    use -- which is what issue #21 is about.

    This script fetches both and drops them where pdfusion-sidecar.spec looks:

        assets/babeldoc/offline_assets_<tag>.zip
        assets/argos/translate-en_vi.argosmodel

    Both are gitignored. Both are optional -- the spec prints a WARN and the
    app falls back to downloading at runtime if either is missing -- so this is
    a step you run once before building an installer, not a build dependency.

    It is deliberately NOT wired into build-sidecar.ps1 or Tauri's
    beforeBundleCommand: it needs the network and takes minutes, and a bundle
    step that silently downloads a third of a gigabyte is the problem, not the
    fix.

.NOTES
    <tag> is a hash of BabelDOC's own asset manifest, so the zip is only valid
    for the babeldoc version it was built against. Re-run this after bumping
    babeldoc; a stale zip is ignored at runtime (the app downloads instead),
    and this script deletes it when it stages a new one.
#>

[CmdletBinding()]
param(
    # Re-fetch even when the asset is already staged.
    [switch] $Force
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

# Same interpreter resolution as build-sidecar.ps1: PDFUSION_PYTHON is a
# persistent user env var that sidecar.rs::locate_python reads too, so it
# survives however the shell was launched. `python` on PATH is the fallback for
# a conda-activated shell.
$PyExe = $null
if ($env:PDFUSION_PYTHON -and (Test-Path $env:PDFUSION_PYTHON)) {
    $PyExe = $env:PDFUSION_PYTHON
}
else {
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) { $PyExe = $pythonCmd.Source }
}
if (-not $PyExe) {
    throw "No Python found. Activate the pdfusion conda env, or set PDFUSION_PYTHON to that env's python.exe."
}

Write-Host "==> Staging offline engine assets with $PyExe" -ForegroundColor Cyan

$Script = @'
import asyncio
import shutil
import sys
from pathlib import Path

repo = Path(sys.argv[1])
force = sys.argv[2] == "1"
babeldoc_dir = repo / "assets" / "babeldoc"
argos_dir = repo / "assets" / "argos"


def stage_babeldoc() -> None:
    # The _async variant, never the sync wrapper: babeldoc runs the sync one
    # through run_in_another_thread, where threading.excepthook swallows the
    # SystemExit its downloaders raise -- a failed build would look like a
    # successful one.
    from babeldoc.assets.assets import (
        generate_offline_assets_package_async,
        get_offline_assets_tag,
    )

    target = babeldoc_dir / f"offline_assets_{get_offline_assets_tag()}.zip"
    if target.exists() and not force:
        print(f"[skip]     {target.name} already staged")
        return

    babeldoc_dir.mkdir(parents=True, exist_ok=True)
    for stale in babeldoc_dir.glob("offline_assets_*.zip"):
        if stale != target:
            stale.unlink()
            print(f"[clean]    removed stale {stale.name}")

    print("[babeldoc] downloading assets and packaging (~210 MB, several minutes)")
    asyncio.run(generate_offline_assets_package_async(babeldoc_dir))
    if not target.exists():
        raise SystemExit(f"expected {target} to exist after packaging")
    size_mb = target.stat().st_size / 1024 / 1024
    print(f"[babeldoc] staged {target.name} ({size_mb:.0f} MB)")


def stage_argos() -> None:
    import argostranslate.package as argos_package

    target = argos_dir / "translate-en_vi.argosmodel"
    if target.exists() and not force:
        print(f"[skip]     {target.name} already staged")
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
    argos_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(entry.download()), target)
    size_mb = target.stat().st_size / 1024 / 1024
    print(f"[argos]    staged {target.name} ({size_mb:.0f} MB)")


stage_babeldoc()
stage_argos()
'@

$forceFlag = if ($Force) { "1" } else { "0" }
$Script | & $PyExe - $RepoRoot $forceFlag
if ($LASTEXITCODE -ne 0) {
    throw "Asset staging failed (exit $LASTEXITCODE)"
}

Write-Host "==> Done. Run ./build-sidecar.ps1 to bundle them." -ForegroundColor Green
