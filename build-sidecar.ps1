#requires -Version 5.1
<#
.SYNOPSIS
    Build the PDFusion Python sidecar and stage it for Tauri (Windows).

.DESCRIPTION
    A launcher, not the build. Everything that decides what gets built lives in
    scripts/build_sidecar.py, which build-sidecar.sh runs too -- so the
    platforms cannot drift (#69). All this file does is resolve an interpreter.

    That resolution is the reason the launcher exists at all: Tauri's
    `beforeBundleCommand` spawns a bare `powershell.exe` that does not inherit a
    conda-activated PATH, so `pnpm tauri build` -- the documented way to build
    the installer -- always arrives with the env's Scripts dir missing.
    PDFUSION_PYTHON is a persistent user env var that `sidecar.rs::locate_python`
    reads too, so it survives however the shell was launched.

.EXAMPLE
    ./build-sidecar.ps1
    ./build-sidecar.ps1 -Stub
#>

[CmdletBinding()]
param(
    # Windows only: the rustc host triple Tauri's `externalBin` wants on the
    # staged filename. Left unset, the build script asks `rustc -vV`.
    [string] $Triple,
    # Skip PyInstaller; just drop placeholder files so Tauri's build script
    # (which validates `externalBin` + `resources` at compile time) is
    # satisfied during `pnpm tauri dev`. The real exe needs a run without it.
    [switch] $Stub
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Script   = Join-Path $RepoRoot "scripts\build_sidecar.py"

# Same order as scripts/_find_python.sh and sidecar.rs::locate_python.
$PyExe = $null
if ($env:PDFUSION_PYTHON -and (Test-Path $env:PDFUSION_PYTHON)) {
    $PyExe = $env:PDFUSION_PYTHON
}
else {
    foreach ($dist in @("anaconda3", "miniconda3", "miniforge3")) {
        foreach ($envName in @("pdfusion", "pdfusion-env")) {
            $candidate = Join-Path $env:USERPROFILE "$dist\envs\$envName\python.exe"
            if (Test-Path $candidate) { $PyExe = $candidate; break }
        }
        if ($PyExe) { break }
    }
}
if (-not $PyExe) {
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) { $PyExe = $pythonCmd.Source }
}
if (-not $PyExe) {
    throw "No Python found. Activate the pdfusion conda env, or set PDFUSION_PYTHON to that env's python.exe."
}

$PyArgs = @($Script)
if ($Stub)   { $PyArgs += "--stub" }
if ($Triple) { $PyArgs += @("--triple", $Triple) }

& $PyExe @PyArgs
if ($LASTEXITCODE -ne 0) {
    throw "Sidecar build failed (exit $LASTEXITCODE)"
}
