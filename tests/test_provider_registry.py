"""The provider registry is complete, agrees with the settings, and is cheap (#83).

Every per-provider rule the backend has is read from `providers/registry.py`,
so a spec missing a field is a provider that half-works: no label in an error
message, no fallback order for chat, a translator that can't be built. These
tests are what makes "adding a provider is one registry entry" safe to rely on.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from desktop_pdf_translator.config import AppSettings, TranslationService
from desktop_pdf_translator.providers.registry import (
    PROVIDERS,
    llm_ids_by_priority,
    provider,
)
from desktop_pdf_translator.translators.base import BaseTranslator

_SRC = Path(__file__).resolve().parent.parent / "src"


def test_ids_are_unique_and_are_the_enum_in_order():
    ids = [spec.id for spec in PROVIDERS]

    assert len(set(ids)) == len(ids)
    # The enum is built from the registry; its order is the OpenAPI enum's and
    # `/config/options`'s.
    assert [service.value for service in TranslationService] == ids


def test_ids_are_frozen():
    """Both translation caches and `config.toml`'s sections key on these, so a
    rename drops every cached paragraph and every saved key."""
    assert [spec.id for spec in PROVIDERS][:4] == ["openai", "gemini", "anthropic", "argos"]


@pytest.mark.parametrize("spec", PROVIDERS, ids=lambda spec: spec.id)
def test_every_spec_is_complete(spec):
    assert spec.label and spec.short_label
    # The Models page's card copy comes from here, not the frontend (#86).
    assert spec.description
    assert spec.protocol in ("openai", "anthropic", "gemini", "argos")
    assert spec.suggested_models, "offer at least the default"
    if spec.requires_key:
        assert spec.env_prefix, "a keyed provider reads its key from the environment"
        assert spec.priority is not None, "a keyed provider is a chat fallback"
        # Listing is how a key is verified without generating text (#84).
        assert spec.lister is not None, "a keyed provider's models are listed"
    if spec.takes_endpoint:
        assert spec.default_base_url
        assert spec.endpoint_hint, "the Override base URL field says what to put there"
        assert spec.lister is not None, "a custom endpoint's models are listed"


@pytest.mark.parametrize("spec", PROVIDERS, ids=lambda spec: spec.id)
def test_the_default_model_is_the_first_suggestion_and_the_settings_default(spec):
    assert spec.suggested_models[0] == spec.default_model
    assert AppSettings().model_for(spec.id) == spec.default_model


@pytest.mark.parametrize("spec", PROVIDERS, ids=lambda spec: spec.id)
def test_the_default_model_is_not_retired(spec):
    """`ConfigManager` would swap a retired default for itself on every load."""
    assert spec.default_model not in spec.retired_models


@pytest.mark.parametrize("spec", PROVIDERS, ids=lambda spec: spec.id)
def test_the_translator_resolves(spec):
    translator = spec.translator()

    assert isinstance(translator, type) and issubclass(translator, BaseTranslator)


@pytest.mark.parametrize(
    "spec", [s for s in PROVIDERS if s.lister], ids=lambda spec: spec.id
)
def test_the_lister_resolves(spec):
    assert callable(spec.lister())


def test_priorities_are_distinct():
    ranked = [spec.priority for spec in PROVIDERS if spec.priority is not None]

    assert len(set(ranked)) == len(ranked)
    assert len(llm_ids_by_priority()) == len(ranked)


@pytest.mark.parametrize("service", ["gemini", TranslationService.GEMINI])
def test_has_api_key_takes_a_member_or_its_value(service):
    """`TranslationSettings` doesn't validate assignment, so a plain string can
    reach it; the old if/elif compared with `==` and took either."""
    settings = AppSettings(providers={"gemini": {"api_key": "k"}})

    assert settings.has_api_key(service)
    assert AppSettings().has_api_key("argos")
    assert not AppSettings().has_api_key("gemini")


def test_an_unknown_id_is_a_value_error():
    with pytest.raises(ValueError, match="Unknown provider"):
        provider("nope")


def test_importing_the_registry_loads_no_sdk_and_no_pydantic():
    """`config.models` imports the registry, and `translators.rate_limiter`
    does too from the boot path; the SDKs cost seconds. Pydantic is not slow,
    but the registry must not need `config`, and pydantic arriving here is the
    first sign that it does."""
    code = (
        "import sys; import desktop_pdf_translator.providers.registry; "
        "print(' '.join(sorted(m for m in "
        "('openai', 'anthropic', 'google.genai', 'pydantic') if m in sys.modules)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(_SRC)},
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == []
