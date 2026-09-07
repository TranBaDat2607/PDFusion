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

FORBIDDEN_AT_BOOT = (
    "torch",
    "chromadb",
    "sentence_transformers",
    "babeldoc",
    "sklearn",
    "camelot",
    "transformers",
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
