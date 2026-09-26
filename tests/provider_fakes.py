"""Stand-ins shared by the `/config` and `/providers` route tests (#84).

The routes reach a provider through exactly one function,
`providers.catalog.list_models`, and keep what it said in the catalog
returned by `providers.catalog.get_model_catalog`. The tests replace the first
with `FakeLister` and point the second at a `ModelCatalog` under `tmp_path`;
nothing here leaves the machine.

`GenerationGuard` is the other half: verifying a key must never generate text,
so any road into a translator — the factory, or `validate_configuration` —
is recorded and refused.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple, Union

import pytest

from desktop_pdf_translator.config import AppSettings, ModelRef
from desktop_pdf_translator.config.manager import ConfigManager
from desktop_pdf_translator.providers import catalog, registry
from desktop_pdf_translator.providers.listing import ListedModel, Listing

OLLAMA = "http://localhost:11434/v1"
ATTACKER = "https://attacker.example/v1"


def make_keyless(monkeypatch: pytest.MonkeyPatch, provider_id: str = "openai") -> None:
    """Swap `provider_id`'s registry entry for a keyless local server, as one
    would be registered for #88 (Ollama et al.): no key, never a chat
    fallback (`priority`), and a placeholder key for an SDK that refuses to
    build a client with none.

    Only `registry._BY_ID` is patched — everything that matters looks a spec
    up through `provider()` — so `PROVIDERS` iteration (`GET /providers`,
    `test_provider_registry.py`'s parametrizations) still sees the original,
    keyed entry.
    """
    spec = registry.provider(provider_id)
    monkeypatch.setitem(
        registry._BY_ID,
        provider_id,
        replace(
            spec,
            requires_key=False,
            placeholder_key="unused",
            env_prefix=None,
            priority=None,
        ),
    )

# What the fake answers with unless a test says otherwise: a chat model with
# every field the record carries, two bare ones, and one the id filter hid.
DEFAULT_LISTING = Listing(
    models=(
        ListedModel(
            id="gpt-4.1",
            display_name="GPT-4.1",
            context_tokens=1_047_576,
            output_tokens=32_768,
        ),
        ListedModel(id="llama3.2:3b"),
        ListedModel(id="qwen3"),
    ),
    hidden=(ListedModel(id="text-embedding-3-small"),),
)


class FakeLister:
    """Replaces `catalog.list_models`, recording `(provider_id, api_key,
    base_url)` for every call — which key went to which endpoint."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Optional[str], Optional[str]]] = []
        self.result: Union[Listing, BaseException] = DEFAULT_LISTING

    async def __call__(
        self, provider_id: Any, api_key: Optional[str], base_url: Optional[str]
    ) -> Listing:
        # `TranslationService` is a str-valued enum; record the plain id.
        self.calls.append((getattr(provider_id, "value", provider_id), api_key, base_url))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def install_lister(monkeypatch: pytest.MonkeyPatch) -> FakeLister:
    fake = FakeLister()
    monkeypatch.setattr(catalog, "list_models", fake)
    return fake


def install_catalog(monkeypatch: pytest.MonkeyPatch, tmp_path) -> catalog.ModelCatalog:
    store = catalog.ModelCatalog(cache_dir=tmp_path / "catalog")
    monkeypatch.setattr(catalog, "get_model_catalog", lambda: store)
    return store


class GenerationGuard:
    """Records every attempt to build a translator or call
    `validate_configuration`, and refuses it."""

    def __init__(self) -> None:
        self.calls: List[str] = []

    def refuse(self, name: str):
        def refused(*_args, **_kwargs):
            self.calls.append(name)
            raise AssertionError(f"{name} was called: verifying must not generate text")

        return refused


def install_factory_guard(monkeypatch: pytest.MonkeyPatch) -> GenerationGuard:
    """Only the factory — cheap to import, and the road every old probe took.
    Keeps a route that still probes the old way off the network."""
    from desktop_pdf_translator.translators.factory import TranslatorFactory

    guard = GenerationGuard()
    monkeypatch.setattr(
        TranslatorFactory, "create_translator", guard.refuse("create_translator")
    )
    return guard


def install_full_guard(monkeypatch: pytest.MonkeyPatch) -> GenerationGuard:
    """The factory plus each LLM translator's `validate_configuration`."""
    from desktop_pdf_translator.translators.anthropic_translator import (
        AnthropicTranslator,
    )
    from desktop_pdf_translator.translators.factory import TranslatorFactory
    from desktop_pdf_translator.translators.gemini_translator import GeminiTranslator
    from desktop_pdf_translator.translators.openai_translator import OpenAITranslator

    guard = GenerationGuard()
    monkeypatch.setattr(
        TranslatorFactory, "create_translator", guard.refuse("create_translator")
    )
    for cls in (OpenAITranslator, GeminiTranslator, AnthropicTranslator):
        monkeypatch.setattr(
            cls,
            "validate_configuration",
            guard.refuse(f"{cls.__name__}.validate_configuration"),
        )
    return guard


def seed(
    manager: ConfigManager,
    preferred_service: Optional[str] = None,
    **sections: Dict[str, Any],
) -> None:
    """Save settings straight through the manager, as a previous session would
    have left them — without `PUT /config`, whose promotion lists and whose
    save invalidates the catalog.

    Each section is a provider's settings, plus `model`: the model that
    provider runs (`AppSettings.model_for`). `preferred_service` makes that
    provider's model the translation model."""
    data = manager.settings.model_dump()
    models = {}
    for service, fields in sections.items():
        fields = dict(fields)
        if "model" in fields:
            models[service] = fields.pop("model")
        data["providers"][service].update(fields)
    settings = AppSettings(**data)
    for service, model in models.items():
        if settings.translation.model.provider.value == service:
            settings.translate_with(ModelRef(provider=service, model=model))
        else:
            settings.remember_model(service, model)
    if preferred_service is not None:
        settings.translate_with(
            ModelRef(provider=preferred_service, model=settings.model_for(preferred_service))
        )
    settings = AppSettings(**settings.model_dump())
    assert manager.save_settings(settings)
    manager._settings = settings


def ids_and_sources(records: List[dict]) -> List[Tuple[str, str]]:
    return [(record["id"], record["source"]) for record in records]


def listed(listing_models) -> List[Tuple[str, str]]:
    return [(model.id, "listed") for model in listing_models]
