"""The sidecar has to print READY before the Tauri shell's deadline.

Everything expensive — BabelDOC, torch, chromadb, sentence-transformers,
camelot — belongs behind a function-level import on the path that needs it. It
is easy to undo by accident: a package `__init__.py` runs whenever *any* of its
submodules is imported, so one re-export in `processors/__init__.py` is enough
to put BabelDOC back in front of the handshake (see issue #19, where importing
`api.server` cost 16 s).

The statement is about a fresh interpreter's `sys.modules`, so each case runs a
subprocess: by the time pytest gets here the heavy modules may already be loaded
by another test.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"

# `sentence_transformers` and `transformers` used to be here. Both left with
# the switch to rag/onnx_embeddings.py: sentence-transformers was the only thing
# that required either, so a clean `pip install -r requirements.txt` no longer
# installs them — stanza and underthesea name transformers only in an extra.
# test_every_forbidden_name_is_a_real_module requires every name in this tuple
# to be installed, so an uninstalled one fails the suite rather than quietly
# guarding nothing. That is the point: a name that cannot be imported at all is
# not evidence about what boot imports.
#
# `torch` and `stanza` stay for the opposite reason — argostranslate
# hard-requires stanza==1.10.1, which requires torch, so pip still installs that
# whole chain into a dev/CI env even though `pdfusion-sidecar.spec` excludes it
# from the shipped bundle. There they are both installed and genuinely worth
# asserting are never imported.
FORBIDDEN_AT_BOOT = (
    "torch",
    "chromadb",
    "stanza",
    "babeldoc",
    "sklearn",
    "camelot",
)


def _run_probe(code: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(_SRC)},
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize(
    "module",
    [
        "desktop_pdf_translator.api.server",
        # The route module that made `translation_cache` expensive by
        # association: it is pure sqlite3, but importing it runs
        # `translators/__init__.py`, which used to pull in the provider SDKs.
        # `processors/__init__.py` is covered by the `api.server` case above,
        # which reaches `processors.pdf_cache` — the same shape, and the one
        # that cost 4.9 s.
        "desktop_pdf_translator.api.routes.config",
        # The setup route exists to install BabelDOC's assets, so it is the
        # module most likely to import babeldoc at the top by accident. Its
        # helper, `engine_assets`, is on the boot path via the /translate
        # pre-flight, and every babeldoc/argostranslate import in both lives
        # inside a function for exactly this reason.
        "desktop_pdf_translator.api.routes.setup",
        "desktop_pdf_translator.engine_assets",
        # The migration runner every SQLite store opens through. Both caches
        # import it, and both are on the boot path via `api.routes.config`.
        "desktop_pdf_translator.storage.migrations",
        # Both imported at module level by `api/routes/rag.py`, so the /rag/ask
        # pre-flight can answer from the records without loading chromadb.
        "desktop_pdf_translator.storage.records",
        "desktop_pdf_translator.rag.index_spec",
        # Also imported at module level by `api/routes/rag.py`, to tell an
        # ask's failures apart.
        "desktop_pdf_translator.rag.errors",
    ],
)
def test_boot_path_does_not_import_the_heavy_stack(module: str) -> None:
    loaded = _run_probe(
        f"import importlib, sys; importlib.import_module({module!r}); "
        f"print(' '.join(m for m in {FORBIDDEN_AT_BOOT!r} if m in sys.modules))"
    )
    assert loaded.split() == []


def test_every_forbidden_name_is_a_real_module() -> None:
    """Guard the guard. The test above passes trivially on a misspelling, and
    `find_spec` settles that without paying to import any of them."""
    missing = _run_probe(
        f"from importlib.util import find_spec; "
        f"print(' '.join(m for m in {FORBIDDEN_AT_BOOT!r} if find_spec(m) is None))"
    )
    assert missing.split() == []
