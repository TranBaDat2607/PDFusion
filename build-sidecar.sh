#!/bin/sh
# Build the PDFusion Python sidecar and stage it for Tauri (Linux, macOS).
#
# A launcher, not the build: everything that decides what gets built lives in
# scripts/build_sidecar.py, which `build-sidecar.ps1` runs too. All this does
# is find the interpreter — see scripts/_find_python.sh for why that is not
# simply `python3`.
#
#   ./build-sidecar.sh          # the real PyInstaller build (~15 min)
#   ./build-sidecar.sh --stub   # placeholders, so cargo/tauri compile at all
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$REPO_ROOT/scripts/_find_python.sh"
pdfusion_find_python

exec "$PDFUSION_PY" "$REPO_ROOT/scripts/build_sidecar.py" "$@"
