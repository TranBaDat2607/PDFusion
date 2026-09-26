"""The translator factory resolves a saved endpoint and key through the
registry before building a translator (#88).

`TranslatorFactory.create_translator` reads a provider's saved `api_key` and
`base_url` straight out of settings and hands them to the translator's
constructor. Two new provider shapes need those resolved first: a provider
that borrows another's API at its own URL needs the registry's
`default_base_url` when nothing was saved (`registry.endpoint_for`), and a
keyless local server needs its `placeholder_key` sent in place of no key at
all (`registry.request_key`) — OpenAI's SDK refuses to build a client with
none. Building a translator never calls its API, so these are built directly
and inspected rather than through a fake.
"""

from __future__ import annotations

import httpx
import pytest

from desktop_pdf_translator.config import AppSettings
from desktop_pdf_translator.translators import factory as factory_module
from desktop_pdf_translator.translators.factory import TranslatorFactory
from desktop_pdf_translator.translators.openai_translator import OpenAITranslator

from provider_fakes import make_keyless


def _settings(**providers) -> AppSettings:
    return AppSettings(providers=providers)


def test_openai_with_a_key_and_no_saved_endpoint_uses_the_specs_default(
    monkeypatch: pytest.MonkeyPatch,
):
    settings = _settings(openai={"api_key": "sk-test"})
    monkeypatch.setattr(factory_module, "get_settings", lambda: settings)

    translator = TranslatorFactory.create_translator(
        service="openai", lang_in="en", lang_out="vi"
    )

    assert isinstance(translator, OpenAITranslator)
    assert translator.base_url == "https://api.openai.com/v1"
    assert translator.api_key == "sk-test"


def test_openai_with_a_saved_endpoint_uses_it(monkeypatch: pytest.MonkeyPatch):
    settings = _settings(
        openai={"api_key": "sk-test", "base_url": "https://openrouter.ai/api/v1"}
    )
    monkeypatch.setattr(factory_module, "get_settings", lambda: settings)

    translator = TranslatorFactory.create_translator(
        service="openai", lang_in="en", lang_out="vi"
    )

    assert translator.base_url == "https://openrouter.ai/api/v1"


def test_a_keyless_variant_builds_fine_with_the_placeholder_key(
    monkeypatch: pytest.MonkeyPatch,
):
    """Ollama et al. (#88): no key saved, but OpenAI's SDK refuses to build a
    client with none — `request_key` must supply the registry's placeholder."""
    make_keyless(monkeypatch, "openai")
    settings = _settings(openai={"base_url": "http://localhost:11434/v1"})
    monkeypatch.setattr(factory_module, "get_settings", lambda: settings)

    translator = TranslatorFactory.create_translator(
        service="openai", lang_in="en", lang_out="vi"
    )

    assert translator.api_key == "unused"


def test_constructing_the_openai_translator_makes_no_network_call(
    monkeypatch: pytest.MonkeyPatch,
):
    """Building a translator is object construction only; the network-touching
    step is `generate`/`translate`, never `__init__`."""

    def refuse(*_args, **_kwargs):
        raise AssertionError("constructing a translator must not touch the network")

    monkeypatch.setattr(httpx.Client, "send", refuse)
    settings = _settings(openai={"api_key": "sk-test"})
    monkeypatch.setattr(factory_module, "get_settings", lambda: settings)

    translator = TranslatorFactory.create_translator(
        service="openai", lang_in="en", lang_out="vi"
    )

    assert isinstance(translator, OpenAITranslator)


# ---------------------------------------------------------------------------
# each provider has a rate limiter of its own
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_limiters(monkeypatch: pytest.MonkeyPatch) -> dict:
    """An empty limiter table, so a rate set here doesn't outlive the test."""
    from desktop_pdf_translator.translators import rate_limiter

    limiters: dict = {}
    monkeypatch.setattr(rate_limiter, "_LIMITERS", limiters)
    return limiters


def test_a_provider_borrowing_openais_translator_runs_under_its_own_limiter(
    monkeypatch: pytest.MonkeyPatch, fresh_limiters: dict
):
    """OpenRouter, DeepSeek and Ollama reuse `OpenAITranslator`. Keyed on the
    class's own name, all four drew on OpenAI's budget, and the registry's
    `default_qps` for the others was never read."""
    from desktop_pdf_translator.providers.registry import provider

    settings = _settings(openai={"api_key": "sk-test"})
    monkeypatch.setattr(factory_module, "get_settings", lambda: settings)

    TranslatorFactory.create_translator(service="ollama", lang_in="en", lang_out="vi")

    assert set(fresh_limiters) == {"ollama"}
    assert fresh_limiters["ollama"]._rate == provider("ollama").default_qps


def test_a_max_qps_saved_for_one_provider_leaves_openais_limiter_alone(
    monkeypatch: pytest.MonkeyPatch, fresh_limiters: dict
):
    from desktop_pdf_translator.providers.registry import provider

    settings = _settings(
        openai={"api_key": "sk-test"}, deepseek={"api_key": "sk-ds", "max_qps": 0.5}
    )
    monkeypatch.setattr(factory_module, "get_settings", lambda: settings)

    TranslatorFactory.create_translator(service="openai", lang_in="en", lang_out="vi")
    TranslatorFactory.create_translator(service="deepseek", lang_in="en", lang_out="vi")

    assert fresh_limiters["openai"]._rate == provider("openai").default_qps
    assert fresh_limiters["deepseek"]._rate == 0.5


# ---------------------------------------------------------------------------
# the paragraph cache is namespaced by provider too (#88 follow-up)
# ---------------------------------------------------------------------------
#
# OpenRouter, DeepSeek and Ollama all build `OpenAITranslator`. If the cache
# key it writes through is only ever "openai" regardless of which of those
# built it, an Ollama server and an OpenAI-compatible endpoint that happen to
# serve a same-named model (e.g. "llama3.2") will silently serve each other's
# cached paragraphs.


class _FakeChatCompletions:
    """Stands in for `client.chat.completions`. Tolerant of whatever kwargs
    `translate()` sends (model, messages, temperature, ...) since the shape
    of the request is not part of this spec — only whether the client gets
    called at all, and how many times."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = type("Message", (), {"content": self._content})()
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"choices": [choice]})()


class _FakeOpenAIClient:
    def __init__(self, content: str) -> None:
        self.completions = _FakeChatCompletions(content)
        self.chat = type("Chat", (), {"completions": self.completions})()


@pytest.fixture
def isolated_paragraph_cache(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """A fresh `TranslationCache` swapped in for the process-wide singleton,
    so a hit or miss here can't leak into (or be caused by) another test.
    Caching is forced on directly, so the test doesn't depend on whatever
    `translation.cache_translations` defaults to.
    """
    from desktop_pdf_translator.translators import translation_cache as tc_module

    cache = tc_module.TranslationCache(cache_dir=tmp_path / "paragraph_cache")
    monkeypatch.setattr(tc_module, "_INSTANCE", cache)
    monkeypatch.setattr(tc_module, "_cache_enabled", lambda: True)
    return cache


# Shared by every translator this file builds: same endpoint, same model —
# only `provider_id` differs, so a fix that keyed the cache on the endpoint
# instead of the provider would still fail test 1 below; only keying it on
# the provider (the spec) separates the two translators' entries.
_SHARED_BASE_URL = "https://shared-endpoint.example/v1"


def _build_openai_translator(provider_id: str, content: str) -> OpenAITranslator:
    translator = OpenAITranslator(
        lang_in="en",
        lang_out="vi",
        api_key="sk-or-unused",
        model="llama3.2",
        base_url=_SHARED_BASE_URL,
        provider_id=provider_id,
    )
    translator.client = _FakeOpenAIClient(content)
    return translator


_PARAGRAPH = (
    "The quarterly reconciliation memorandum for the Solstice Archive "
    "Initiative must be rendered before the fiscal cutover, unabridged."
)


def test_a_provider_sharing_openais_translator_does_not_serve_its_cached_paragraph(
    isolated_paragraph_cache, fresh_limiters: dict
):
    """Ollama translates the paragraph first; the OpenAI-built translator,
    same model name and languages, must call its own client rather than be
    served Ollama's cached text."""
    ollama = _build_openai_translator("ollama", "Bản dịch Ollama")
    openai = _build_openai_translator("openai", "Bản dịch OpenAI")

    ollama_result = ollama.translate(_PARAGRAPH)
    openai_result = openai.translate(_PARAGRAPH)

    assert ollama_result == "Bản dịch Ollama"
    assert openai_result == "Bản dịch OpenAI"
    assert len(openai.client.completions.calls) == 1


def test_two_translators_for_the_same_provider_do_share_the_cache(
    isolated_paragraph_cache, fresh_limiters: dict
):
    """Guard: same provider id and model, so the second translator must be
    served the first's cached translation without calling its own client —
    proving the cache is actually wired into `translate()` in this setup, so
    the test above failing after the fix would mean something."""
    first = _build_openai_translator("openai", "Bản dịch OpenAI (first)")
    second = _build_openai_translator("openai", "Bản dịch OpenAI (second)")

    first_result = first.translate(_PARAGRAPH)
    second_result = second.translate(_PARAGRAPH)

    assert first_result == "Bản dịch OpenAI (first)"
    assert second_result == "Bản dịch OpenAI (first)"
    assert len(second.client.completions.calls) == 0
