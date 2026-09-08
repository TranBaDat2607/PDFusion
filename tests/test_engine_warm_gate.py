"""The boot warm thread never downloads engine assets (#23 follow-up).

`_warm_translation_engine` loads BabelDOC's layout model so the first chunk of
the first job doesn't pay for it. `DocLayoutModel.load_available()` *downloads*
that model when it isn't cached, and this thread runs at boot with nothing on
screen to report a download or fail it — the same reason `_should_prewarm_argos`
gates on `argos_pack_ready()`. So the load is gated on `babeldoc_core_ready()`.

Importing BabelDOC itself stays unconditional: it touches no assets.

The `processors.processor` import is faked out here rather than performed — it
costs ~7 s (babeldoc + torch), which is the whole point of `_warm_translation_engine`
being a background thread, and this suite is meant to stay fast.
"""

from __future__ import annotations

import types

import pytest

from desktop_pdf_translator import engine_assets
from desktop_pdf_translator.api import server
from desktop_pdf_translator.processors import doc_layout_cache
import desktop_pdf_translator.processors as processors_pkg


@pytest.fixture
def loads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record calls to the layout-model loader, and stub the heavy import.

    `from ..processors import processor` resolves through `getattr` on the
    package before it reaches the import machinery, so setting the attribute is
    enough to keep babeldoc + torch out of this test.
    """
    monkeypatch.setattr(
        processors_pkg, "processor", types.ModuleType("processor"), raising=False
    )
    calls: list[int] = []
    monkeypatch.setattr(
        doc_layout_cache,
        "get_shared_doc_layout_model",
        lambda: calls.append(1),
    )
    return calls


def test_loads_the_model_when_the_asset_is_installed(monkeypatch, loads):
    monkeypatch.setattr(engine_assets, "babeldoc_core_ready", lambda: True)

    server._warm_translation_engine()

    assert loads == [1]


def test_skips_the_model_when_the_asset_is_missing(monkeypatch, loads):
    """A fresh install where the user hasn't run setup yet. Loading here would
    start an unattended download, and can corrupt the setup flow's own copy:
    `babeldoc.assets.download_file` writes in place with no temp + rename."""
    monkeypatch.setattr(engine_assets, "babeldoc_core_ready", lambda: False)

    server._warm_translation_engine()

    assert loads == []
