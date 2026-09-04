"""Shared pytest fixtures.

`src/` is a src-layout package that is normally installed with `pip install -e .`.
Adding it to `sys.path` here keeps `pytest` runnable straight from a checkout
without an editable install.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# A minimal but structurally real PDF, so tests exercise the `%PDF-` header
# check instead of working around it.
MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n"
    b"%%EOF\n"
)


@pytest.fixture
def temp_translate_dir(tmp_path: Path) -> Path:
    """Stand-in for `%TEMP%\\pdfusion-translate-<rand>\\` — the throwaway dir a
    translation job writes its rolling output into."""
    d = tmp_path / "pdfusion-translate-abc123"
    d.mkdir()
    return d


@pytest.fixture
def rolling_pdf(temp_translate_dir: Path) -> Path:
    """The artifact a finished job hands the frontend."""
    path = temp_translate_dir / "paper_translated_v003.pdf"
    path.write_bytes(MINIMAL_PDF)
    return path


@pytest.fixture
def empty_rolling_pdf(temp_translate_dir: Path) -> Path:
    """A 0-byte rolling file — what's on disk when BabelDOC produced nothing
    usable. Saving it would hand the user a PDF that silently fails to open."""
    path = temp_translate_dir / "paper_translated_v001.pdf"
    path.write_bytes(b"")
    return path


@pytest.fixture
def deny_copyfile(monkeypatch: pytest.MonkeyPatch):
    """Make the export's copy fail with EACCES. Read-only dirs aren't reliably
    enforced on Windows, so the failure is driven from `copyfile` itself; what
    matters is how the error is classified, not how the OS produced it. Kept
    here because the fully-qualified patch target couples both suites to the
    module path."""

    def deny(*_args, **_kwargs):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(
        "desktop_pdf_translator.utils.file_export.shutil.copyfile", deny
    )
