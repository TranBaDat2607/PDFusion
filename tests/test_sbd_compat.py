"""The stanza stub, and the MiniSBD pin that makes it safe.

`argostranslate.sbd` does an unguarded top-level `import stanza`, and stanza
imports torch — that one line is what put 466 MB of tensor library on the
offline-translate path. The shipped bundle excludes stanza and satisfies the
import with an empty module; these tests cover both halves of why that is safe.

Deliberately avoids importing argostranslate at module scope: it costs seconds
and this suite is meant to stay in-process (see test_sidecar_boot.py).
"""

import importlib.abc
import subprocess
import sys
from pathlib import Path

import pytest

from desktop_pdf_translator.translators._sbd_compat import install_stanza_stub

_SRC = Path(__file__).resolve().parents[1] / "src"


class _Blocker(importlib.abc.MetaPathFinder):
    """Make a package look uninstalled, the way the frozen bundle's excludes do."""

    def __init__(self, blocked):
        self.blocked = blocked

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in self.blocked:
            raise ImportError(f"blocked: {fullname}")
        return None


@pytest.fixture
def without_stanza(monkeypatch):
    monkeypatch.delitem(sys.modules, "stanza", raising=False)
    blocker = _Blocker({"stanza"})
    monkeypatch.setattr(sys, "meta_path", [blocker, *sys.meta_path])
    yield
    sys.modules.pop("stanza", None)


def test_stub_is_installed_when_stanza_is_missing(without_stanza):
    assert install_stanza_stub() is True
    assert sys.modules["stanza"].__pdfusion_stub__ is True


def test_stub_is_idempotent(without_stanza):
    install_stanza_stub()
    first = sys.modules["stanza"]
    assert install_stanza_stub() is True
    assert sys.modules["stanza"] is first


def test_a_real_stanza_is_left_alone():
    # In a dev/CI env argostranslate pulls the real stanza in, and shadowing it
    # with an empty module would break anything that legitimately used it.
    pytest.importorskip("stanza")
    sys.modules.pop("stanza", None)
    assert install_stanza_stub() is False
    assert "stanza" not in sys.modules


def test_argos_translate_imports_and_splits_sentences_without_torch():
    """The whole point, end to end, in a subprocess with the torch stack gone.

    Runs out-of-process because it has to import argostranslate (seconds, and
    unwinding that import is not something a fixture can do), and because the
    only honest way to assert "torch never arrives" is a fresh interpreter.
    """
    code = f"""
import importlib.abc, sys
BLOCKED = {{"torch", "stanza", "transformers", "sentence_transformers"}}

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in BLOCKED:
            raise ImportError("blocked: " + fullname)

sys.meta_path.insert(0, Blocker())
sys.path.insert(0, {str(_SRC)!r})

from desktop_pdf_translator.translators import argos_translator
argos_translator._configure_argos_settings()

import argostranslate.settings as s
import argostranslate.translate  # the import that used to pull torch

assert s.chunk_type is s.ChunkType.MINISBD, s.chunk_type
leaked = sorted(m for m in sys.modules if m.split(".")[0] in BLOCKED)
assert leaked == ["stanza"], leaked           # the stub, and nothing else
assert sys.modules["stanza"].__pdfusion_stub__ is True
print("OK")
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=300
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


@pytest.mark.smoke
def test_argos_actually_translates_without_the_torch_stack():
    """A real en→vi translation with torch, stanza, transformers and
    sentence-transformers all unimportable — which is what
    `pdfusion-sidecar.spec` excludes them makes true in the shipped bundle.

    Marked `smoke`: it loads CTranslate2 and needs the en→vi pack on disk, so
    it is too slow and too environment-dependent for the default run. The
    import-only test above is the fast guard.
    """
    code = f"""
import importlib.abc, sys
BLOCKED = {{"torch", "stanza", "transformers", "sentence_transformers"}}

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in BLOCKED:
            raise ImportError("blocked: " + fullname)

sys.meta_path.insert(0, Blocker())
sys.path.insert(0, {str(_SRC)!r})

from desktop_pdf_translator.translators import argos_translator
# CPU: this asserts the sbd/import path, and a CUDA build mismatch on the
# runner would fail it for an unrelated reason.
argos_translator._detect_device = lambda: "cpu"

t = argos_translator.ArgosTranslator(lang_in="en", lang_out="vi")
out = t.translate("The layout model is loaded once. This is a second sentence.")

assert out.strip(), "empty translation"
assert "layout model" not in out, out          # it really translated
assert t.failed_translations == 0, out
# Two source sentences in, two out -- MiniSBD split them.
assert len([ln for ln in out.splitlines() if ln.strip()]) == 2, out
print("OK")
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=900
    )
    assert result.returncode == 0, result.stderr[-3000:]
    assert "OK" in result.stdout
