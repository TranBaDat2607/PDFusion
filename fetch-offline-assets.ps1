#requires -Version 5.1
<#
.SYNOPSIS
    Stage the translation-engine assets the installer ships, into assets/
    (Windows).

.DESCRIPTION
    A launcher, not the staging. What gets fetched, and why, lives in
    scripts/fetch_offline_assets.py, which fetch-offline-assets.sh runs too
    (#69). All this file does is resolve an interpreter, the same way
    build-sidecar.ps1 does.

.EXAMPLE
    ./fetch-offline-assets.ps1
    ./fetch-offline-assets.ps1 -Force
#>

[CmdletBinding()]
param(
    # Re-fetch even when the asset is already staged.
    [switch] $Force
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Script   = Join-Path $RepoRoot "scripts\fetch_offline_assets.py"

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
if ($Force) { $PyArgs += "--force" }

& $PyExe @PyArgs
if ($LASTEXITCODE -ne 0) {
    throw "Asset staging failed (exit $LASTEXITCODE)"
}
