"""The language contract between the toolbar, the API and the pipeline (#12).

Deliberately imports only `api.schemas`, `translators.capabilities` and
`processors.pdf_cache` — never `api.routes.translation`, which drags in
BabelDOC + torch and turns a sub-second run into a minute-long one (the same
trade-off `test_pdf_export_api.py` documents). Everything asserted here is a
pure decision, so nothing is lost by staying out of the heavy modules.
"""

from __future__ import annotations

import pytest

from desktop_pdf_translator.api.schemas import PrewarmRequest, TranslateRequest
from desktop_pdf_translator.config import LanguageCode, TranslationService
from desktop_pdf_translator.processors.pdf_cache import _make_cache_key
from desktop_pdf_translator.translators.capabilities import (
    normalize_pair,
    resolve_effective_service,
    resolve_languages,
    supported_pairs_for,
    unsupported_reason,
)


class _FakeTranslationSettings:
    def __init__(self, source: LanguageCode, target: LanguageCode):
        self.default_source_lang = source
        self.default_target_lang = target


class _FakeSettings:
    """Just the surface `capabilities` touches, so the tests don't need a real
    config file on disk."""

    def __init__(
        self,
        source: LanguageCode = LanguageCode.ENGLISH,
        target: LanguageCode = LanguageCode.JAPANESE,
        keyed: tuple[TranslationService, ...] = (),
    ):
        self.translation = _FakeTranslationSettings(source, target)
        self._keyed = set(keyed)

    def has_api_key(self, service: TranslationService) -> bool:
        if service == TranslationService.ARGOS:
            return True
        return service in self._keyed


# ---------------------------------------------------------------------------
# Request schema — "unspecified" must be None, never a sentinel
# ---------------------------------------------------------------------------


def test_translate_request_leaves_languages_unspecified():
    """The regression this issue is about: non-null defaults here silently
    pre-empted the configured default and pinned every run to Vietnamese."""
    request = TranslateRequest(file_path="paper.pdf")
    assert request.source_lang is None
    assert request.target_lang is None
    assert request.service is None


def test_translate_request_keeps_explicit_languages():
    request = TranslateRequest(
        file_path="paper.pdf", source_lang="en", target_lang="ja"
    )
    assert request.source_lang is LanguageCode.ENGLISH
    assert request.target_lang is LanguageCode.JAPANESE


def test_prewarm_request_leaves_languages_unspecified():
    payload = PrewarmRequest()
    assert payload.source_lang is None
    assert payload.target_lang is None


# ---------------------------------------------------------------------------
# Default resolution
# ---------------------------------------------------------------------------


def test_unspecified_languages_fall_back_to_configured_defaults():
    settings = _FakeSettings(
        source=LanguageCode.ENGLISH, target=LanguageCode.JAPANESE
    )
    assert resolve_languages(settings, None, None) == (
        LanguageCode.ENGLISH,
        LanguageCode.JAPANESE,
    )


def test_a_request_without_languages_uses_the_configured_defaults():
    """End of the wire: nothing between `TranslateRequest` and the pipeline may
    re-introduce a default of its own."""
    settings = _FakeSettings(
        source=LanguageCode.AUTO, target=LanguageCode.CHINESE_SIMPLIFIED
    )
    request = TranslateRequest(file_path="paper.pdf")
    assert resolve_languages(settings, request.source_lang, request.target_lang) == (
        LanguageCode.AUTO,
        LanguageCode.CHINESE_SIMPLIFIED,
    )


def test_explicit_languages_win_over_configuration():
    settings = _FakeSettings(target=LanguageCode.VIETNAMESE)
    _, target = resolve_languages(settings, None, LanguageCode.JAPANESE)
    assert target is LanguageCode.JAPANESE


# ---------------------------------------------------------------------------
# Effective service
# ---------------------------------------------------------------------------


def test_llm_without_a_key_falls_back_to_argos():
    settings = _FakeSettings(keyed=())
    assert (
        resolve_effective_service(settings, TranslationService.OPENAI)
        is TranslationService.ARGOS
    )


def test_llm_with_a_key_is_kept():
    settings = _FakeSettings(keyed=(TranslationService.OPENAI,))
    assert (
        resolve_effective_service(settings, TranslationService.OPENAI)
        is TranslationService.OPENAI
    )


# ---------------------------------------------------------------------------
# Capability matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "service, lang_in, lang_out",
    [
        (TranslationService.ARGOS, "en", "vi"),
        # Argos has no language detection and pins "auto" to English itself.
        (TranslationService.ARGOS, "auto", "vi"),
        (TranslationService.ARGOS, LanguageCode.AUTO, LanguageCode.VIETNAMESE),
        # LLMs are unrestricted.
        (TranslationService.OPENAI, "en", "ja"),
        (TranslationService.ANTHROPIC, "zh-tw", "vi"),
    ],
)
def test_supported_pairs_are_accepted(service, lang_in, lang_out):
    assert unsupported_reason(service, lang_in, lang_out) is None


@pytest.mark.parametrize("target", ["ja", "zh-cn", "zh-tw", "en"])
def test_argos_rejects_targets_it_has_no_pack_for(target):
    reason = unsupported_reason(TranslationService.ARGOS, "en", target)
    assert reason is not None
    # The message is user-facing — it must name the way out, not just the fault.
    assert "API key" in reason


def test_auto_only_substitutes_for_backends_without_detection():
    assert normalize_pair(TranslationService.ARGOS, "auto", "vi") == ("en", "vi")
    # LLMs detect from content, so "auto" stays "auto" for them.
    assert normalize_pair(TranslationService.OPENAI, "auto", "vi") == ("auto", "vi")


def test_wire_format_expands_auto_aliases_for_the_frontend():
    """The frontend does a plain membership test, so the substitution rule must
    already be applied server-side."""
    assert supported_pairs_for(TranslationService.ARGOS) == [
        ["auto", "vi"],
        ["en", "vi"],
    ]
    assert supported_pairs_for(TranslationService.OPENAI) is None


# ---------------------------------------------------------------------------
# PDF cache key
# ---------------------------------------------------------------------------


def _key(lang_in: str, lang_out: str) -> str:
    return _make_cache_key("f" * 64, lang_in, lang_out, "openai", "gpt-4.1", "1")


def test_cache_key_separates_source_languages():
    """Source is part of the key now that it reaches the pipeline: the LLM
    system prompt names it, so "auto" and "en" can yield different bytes."""
    assert _key("auto", "vi") != _key("en", "vi")


def test_cache_key_separates_target_languages():
    assert _key("en", "vi") != _key("en", "ja")


def test_cache_key_is_stable_for_identical_inputs():
    assert _key("en", "vi") == _key("en", "vi")
