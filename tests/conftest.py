"""Shared pytest fixtures.

`src/` is a src-layout package that is normally installed with `pip install -e .`.
Adding it to `sys.path` here keeps `pytest` runnable straight from a checkout
without an editable install.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Every store resolves its root through `utils/paths.appdata_dir()`. Point that
# at a throwaway folder for the whole run, so nothing a test does can open,
# write or migrate the developer's real data root. A default run during #59 did
# migrate a real paragraph cache; the path that reached it was never pinned to
# one test (a worker thread outliving its test fits the evidence), which is why
# this is process-wide and set at import — ahead of every test module, fixture
# and thread. Subprocesses such as `test_sidecar_boot.py`'s inherit it.
#
# `PDFUSION_DATA_DIR` is the override the Tauri shell itself uses and the only
# one honoured on every platform, so it is what pins the root here; the suite
# would otherwise land in `%LOCALAPPDATA%` on Windows and `~/.local/share` on
# Linux (#69). `LOCALAPPDATA` is still set because the Windows *resolution*
# reads it, and a test that clears the override must still not find the real
# one underneath.
_TEST_LOCALAPPDATA = Path(tempfile.mkdtemp(prefix="pdfusion-tests-"))
os.environ["LOCALAPPDATA"] = str(_TEST_LOCALAPPDATA)
os.environ["PDFUSION_DATA_DIR"] = str(_TEST_LOCALAPPDATA / "PDFusion")
# An empty config, so `ConfigManager` runs on defaults, as it does in CI,
# rather than adopting the developer's own `config.toml` from the home-based
# legacy root (`utils/paths.adopt_legacy_config`).
(_TEST_LOCALAPPDATA / "PDFusion").mkdir()
(_TEST_LOCALAPPDATA / "PDFusion" / "config.toml").write_text("", encoding="utf-8")
atexit.register(shutil.rmtree, _TEST_LOCALAPPDATA, ignore_errors=True)


@pytest.fixture(autouse=True)
def _no_real_keystore(monkeypatch: pytest.MonkeyPatch):
    """Keep every test off the developer's real Secret Service / Keychain.

    The companion to the data-root pinning above, and needed for the same
    reason (#69): off Windows, `save_settings` files a master key in the OS
    keystore, so a plain `pytest` run would create — and later decrypt against
    — a live `PDFusion / config-encryption-key` entry in the developer's login
    keyring. Answering "no keystore here" makes every run behave like a CI
    runner, which is the one environment the fallback path is written for.

    Autouse, so it is in place before any explicitly-requested fixture;
    `test_config_security.py`'s `fake_keystore` overrides it for the tests that
    are *about* the keystore.
    """
    from desktop_pdf_translator.utils import encryption

    monkeypatch.setattr(encryption, "_usable_keyring", lambda: None)
    encryption._reset_master_key_cache()
    yield
    encryption._reset_master_key_cache()


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
