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
from desktop_pdf_translator.processors import doc_layout_cache, font_verify_cache
import desktop_pdf_translator.processors as processors_pkg


@pytest.fixture
def loads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record calls to the layout-model loader, and stub the heavy import.

    `from ..processors import processor` resolves through `getattr` on the
    package before it reaches the import machinery, so setting the attribute is
    enough to keep babeldoc + torch out of this test.

    The font pre-check is stubbed too: run for real it imports babeldoc, hashes
    the developer's whole font cache and leaves `verify_file` patched for the
    rest of the session. The tests that are about it re-stub it with recorders.
    """
    monkeypatch.setattr(
        processors_pkg, "processor", types.ModuleType("processor"), raising=False
    )
    monkeypatch.setattr(font_verify_cache, "install", lambda: None)
    monkeypatch.setattr(font_verify_cache, "preverify_cached_fonts", lambda: 0)
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


# ---------------------------------------------------------------------------
# Font hash memo (BabelDOC re-hashes every cached font per pipeline stage)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("core_ready", [True, False])
def test_warm_up_preverifies_the_fonts(monkeypatch, loads, core_ready):
    """The pre-check only reads fonts already on disk, so it needs no
    `babeldoc_core_ready()` gate. (It installs the memo itself — pinned by
    `test_preverified_fonts_are_not_hashed_again_by_babeldoc`.)"""
    monkeypatch.setattr(engine_assets, "babeldoc_core_ready", lambda: core_ready)
    calls: list[str] = []
    monkeypatch.setattr(
        font_verify_cache, "preverify_cached_fonts", lambda: calls.append("preverify") or 0
    )

    server._warm_translation_engine()

    assert calls == ["preverify"]


def test_a_failing_font_precheck_does_not_cost_the_layout_model_warm_up(monkeypatch, loads):
    monkeypatch.setattr(engine_assets, "babeldoc_core_ready", lambda: True)

    def boom():
        raise RuntimeError("font pre-check failed")

    monkeypatch.setattr(font_verify_cache, "preverify_cached_fonts", boom)

    server._warm_translation_engine()

    assert loads == [1]


# ---------------------------------------------------------------------------
# Argos is warmed when it is what would run
# ---------------------------------------------------------------------------


def _translating_with(provider_id: str, **providers):
    from desktop_pdf_translator.config import AppSettings
    from desktop_pdf_translator.providers.registry import provider

    return AppSettings(
        providers=providers,
        translation={"model": {"provider": provider_id, "model": provider(provider_id).default_model}},
    )


@pytest.fixture
def pack_ready(monkeypatch):
    monkeypatch.setattr(engine_assets, "argos_pack_ready", lambda: True)


def test_argos_is_warmed_for_an_llm_whose_key_is_missing(pack_ready):
    """The sidecar runs Argos in its place — even when another LLM has a key."""
    settings = _translating_with("openai", gemini={"api_key": "sk-gemini"})

    assert server._should_prewarm_argos(settings) is True


def test_argos_is_not_warmed_for_an_llm_with_its_key(pack_ready):
    assert server._should_prewarm_argos(_translating_with("openai", openai={"api_key": "sk"})) is False
