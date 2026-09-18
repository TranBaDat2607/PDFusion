#!/bin/sh
# Stage the ~290 MB of translation-engine assets the installer ships, into
# assets/ (Linux, macOS). See scripts/fetch_offline_assets.py for what and why;
# this only finds the interpreter.
#
#   ./fetch-offline-assets.sh           # skips whatever is already staged
#   ./fetch-offline-assets.sh --force   # re-fetch regardless
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$REPO_ROOT/scripts/_find_python.sh"
pdfusion_find_python

exec "$PDFUSION_PY" "$REPO_ROOT/scripts/fetch_offline_assets.py" "$@"
