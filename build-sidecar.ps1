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

$RepoRoot   = Split-Path -Parent $MyInvocation.MyCommand.Path
$SpecPath   = Join-Path $RepoRoot "pdfusion-sidecar.spec"
$DistDir    = Join-Path $RepoRoot "dist\pdfusion-sidecar"
$StageDir   = Join-Path $RepoRoot "desktop\src-tauri\binaries"
$StagedExe  = Join-Path $StageDir "pdfusion-sidecar-$Triple.exe"
$StagedInternal = Join-Path $StageDir "_internal"

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

# Make sure pyinstaller is available in the active Python env.
$pyinstaller = Get-Command pyinstaller -ErrorAction SilentlyContinue
if (-not $pyinstaller) {
    throw "pyinstaller not found on PATH. Activate the pdfusion conda env and run: pip install pyinstaller"
}

# Run PyInstaller. --clean wipes build/, --noconfirm overwrites dist/ silently.
& pyinstaller $SpecPath --clean --noconfirm

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
