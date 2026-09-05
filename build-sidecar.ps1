#requires -Version 5.1
<#
.SYNOPSIS
    Build the PDFusion Python sidecar with PyInstaller and stage it for Tauri.

.DESCRIPTION
    Runs PyInstaller against pdfusion-sidecar.spec, then mirrors the resulting
    one-dir output into desktop/src-tauri/binaries/ using the triple-suffixed
    filename Tauri's `externalBin` requires on Windows.

    Run from a shell with the `pdfusion` conda env active so the Python deps
    (and `pyinstaller`) are importable.

.NOTES
    Tauri's externalBin path resolution expects:
        binaries/pdfusion-sidecar-<rustc-host-triple>.exe
    On Windows x64 that triple is x86_64-pc-windows-msvc.
#>

[CmdletBinding()]
param(
    [string] $Triple = "x86_64-pc-windows-msvc",
    # Skip PyInstaller; just drop empty placeholder files so Tauri's build
    # script (which validates `externalBin` + `resources` at compile time)
    # is satisfied during `pnpm tauri dev`. The real exe is produced by
    # running this script without -Stub.
    [switch] $Stub
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

$RepoRoot       = Split-Path -Parent $MyInvocation.MyCommand.Path
$SpecPath       = Join-Path $RepoRoot "pdfusion-sidecar.spec"
$DistDir        = Join-Path $RepoRoot "dist\pdfusion-sidecar"
$SrcTauriDir    = Join-Path $RepoRoot "desktop\src-tauri"
$StageDir       = Join-Path $SrcTauriDir "binaries"
$StagedExe      = Join-Path $StageDir "pdfusion-sidecar-$Triple.exe"
# _internal/ must install SIBLING to the renamed externalBin exe at install
# time (PyInstaller's onedir bootloader hardcodes a sibling lookup for
# python313.dll, base_library.zip, etc.). Tauri's `externalBin` renames the
# exe to drop the triple and drops it at the install root, but `resources`
# globs preserve their path from src-tauri/. So we stage _internal/ directly
# under src-tauri/ (not under binaries/) and tauri.conf.json ships it as
# `_internal/**/*` → installs to <install>/_internal/, next to the renamed
# pdfusion-sidecar.exe.
$StagedInternal = Join-Path $SrcTauriDir "_internal"

if (-not (Test-Path $StageDir)) {
    New-Item -ItemType Directory -Force -Path $StageDir | Out-Null
}

if ($Stub) {
    Write-Host "==> Writing stub binaries (no PyInstaller)" -ForegroundColor Yellow
    if (-not (Test-Path $StagedExe))      { New-Item -ItemType File -Path $StagedExe | Out-Null }
    if (-not (Test-Path $StagedInternal)) { New-Item -ItemType Directory -Path $StagedInternal | Out-Null }
    $stubMarker = Join-Path $StagedInternal ".placeholder"
    if (-not (Test-Path $stubMarker)) { New-Item -ItemType File -Path $stubMarker | Out-Null }
    Write-Host "==> Stubs ready. Cargo / Tauri build steps will compile but the bundled sidecar won't actually launch." -ForegroundColor Yellow
    Write-Host "    Run this script without -Stub to produce the real exe."
    return
}

Write-Host "==> Building PDFusion sidecar with PyInstaller" -ForegroundColor Cyan

if (-not (Test-Path $SpecPath)) {
    throw "Spec file not found at $SpecPath"
}

# Resolve PyInstaller. `pyinstaller` on PATH is the happy path, but it is not
# reachable on the one path that matters most: Tauri's `beforeBundleCommand`
# spawns a bare `powershell.exe` that does not inherit a conda-activated PATH,
# so `pnpm tauri build` -- the documented way to build the installer -- always
# arrived here with the env's Scripts dir missing and threw. Fall back to the
# interpreter PDFUSION_PYTHON already names: it is a persistent user env var
# that `sidecar.rs::locate_python` reads too, so it survives however the shell
# was launched.
$PyExe  = $null
$PyArgs = @()

$pyinstallerCmd = Get-Command pyinstaller -ErrorAction SilentlyContinue
if ($pyinstallerCmd) {
    $PyExe = $pyinstallerCmd.Source
}
elseif ($env:PDFUSION_PYTHON -and (Test-Path $env:PDFUSION_PYTHON)) {
    Write-Host "==> pyinstaller not on PATH; using PDFUSION_PYTHON -m PyInstaller" -ForegroundColor Yellow
    $PyExe  = $env:PDFUSION_PYTHON
    $PyArgs = @("-m", "PyInstaller")
}
else {
    throw "pyinstaller not found on PATH, and PDFUSION_PYTHON is unset or does not exist. Activate the pdfusion conda env and run: pip install pyinstaller -- or set PDFUSION_PYTHON to that env's python.exe."
}

# Run PyInstaller. --clean wipes build/, --noconfirm overwrites dist/ silently.
# --distpath/--workpath are not optional niceties: PyInstaller defaults both to
# paths relative to the *current directory*, and Tauri runs beforeBundleCommand
# from desktop/. Without them the onedir tree lands in desktop/dist/ -- which is
# `frontendDist`, so a ~1 GB copy of the Python runtime would be bundled into
# the installer as frontend assets -- while $DistDir below still points at the
# repo root and the script throws "PyInstaller output missing".
& $PyExe @PyArgs $SpecPath --clean --noconfirm `
    --distpath (Join-Path $RepoRoot "dist") `
    --workpath (Join-Path $RepoRoot "build")

if (-not (Test-Path (Join-Path $DistDir "pdfusion-sidecar.exe"))) {
    throw "PyInstaller output missing: $DistDir\pdfusion-sidecar.exe"
}

Write-Host "==> Staging into $StageDir" -ForegroundColor Cyan

# Clean previous staged copy. -Force removes read-only files; -Recurse for the
# _internal/ directory tree.
if (Test-Path $StagedExe)      { Remove-Item -Force $StagedExe }
if (Test-Path $StagedInternal) { Remove-Item -Recurse -Force $StagedInternal }

if (-not (Test-Path $StageDir)) {
    New-Item -ItemType Directory -Force -Path $StageDir | Out-Null
}

# Tauri's externalBin requires the rustc host triple suffix on the filename.
Copy-Item -Path (Join-Path $DistDir "pdfusion-sidecar.exe") -Destination $StagedExe

# Mirror the _internal/ tree (Python stdlib, native .pyd, package data).
Copy-Item -Recurse -Path (Join-Path $DistDir "_internal") -Destination $StagedInternal

Write-Host "==> Done." -ForegroundColor Green
Write-Host "    exe:       $StagedExe"
Write-Host "    internals: $StagedInternal"
