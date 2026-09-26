"""The one place that says which providers exist and how each one differs (#83).

Every per-provider rule the backend used to spell out in its own `if/elif`
chain — labels, default and suggested models, rate limits, retired models,
which providers take a key or an endpoint, the order chat falls back in — is a
field of a `ProviderSpec` here, and the rest of the code reads it from here.

Stdlib only. `config.models` builds `TranslationService` from `PROVIDERS`, and
`rate_limiter` (imported by `translators.base`) reads its defaults here, so this
module is on the sidecar's boot path. It must never import `config` (that would
be a cycle), pydantic, or a provider SDK — `tests/test_provider_registry.py`
checks the last two in a fresh interpreter.

The translator class and the model lister are *callables* that import on first
use, not "module:attr" strings. Both keep the SDKs off the boot path, but
PyInstaller finds an import inside a function by static analysis and cannot
see one named in a string: strings would silently leave the translator
modules out of the frozen sidecar, since nothing else imports them.

Provider ids are frozen. Both translation caches key on them
(`translation_cache._make_cache_key`, `pdf_cache`), and so do the sections of
`config.toml`; renaming one would drop every cached paragraph and saved key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, FrozenSet, List, Literal, Optional, Tuple

Protocol = Literal["openai", "anthropic", "gemini", "argos"]
ModelLister = Callable[[str, Optional[str]], List[str]]


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    # For narrow places — a tab row, a toolbar button — where `label` is too wide.
    short_label: str
    # The API it speaks. Several providers can share one: a server that speaks
    # OpenAI's API is reached with OpenAI's SDK and translator.
    protocol: Protocol
    default_model: str
    # Offered in the model field, default first. Not a whitelist: the field takes
    # any name, which a local server's models need (#32). A default missing
    # from here is how `gemini-1.5-flash` stayed on offer after Google retired
    # it; `tests/test_provider_registry.py` holds the two together.
    suggested_models: Tuple[str, ...]
    # Returns the `BaseTranslator` subclass. Imports on call; see the module doc.
    translator: Callable[[], type]
    requires_key: bool = True
    # `OPENAI` → `OPENAI_API_KEY` / `OPENAI_MODEL` in the environment or `.env`.
    env_prefix: Optional[str] = None
    # Whether it can be pointed at another server speaking its API: Ollama,
    # LM Studio, a proxy (#32).
    takes_endpoint: bool = False
    # Where requests go with no endpoint of the user's own.
    default_base_url: Optional[str] = None
    # Returns `list(api_key, base_url) -> model ids`, for a custom endpoint whose
    # models are in no list we could ship. Imports on call.
    lister: Optional[Callable[[], ModelLister]] = None
    # Requests/sec its shared limiter runs at with no `max_qps` configured.
    # Provider limits vary by two orders of magnitude across tiers, so these
    # are conservative starting points, not measured ceilings.
    default_qps: Optional[float] = None
    # Lower goes first when a choice falls to "any LLM with a key": the chat
    # answer model (`rag_chain._answer_model`) and the Argos → LLM promotion
    # in `PUT /config`. `None` for a provider that is never such a fallback.
    priority: Optional[int] = None
    # Every (source, target) pair it can produce; `None` for unrestricted. The
    # LLMs prompt for an arbitrary target language. Argos is an NMT model with
    # one installed pack, and broadening it means shipping more packs.
    supported_pairs: Optional[FrozenSet[Tuple[str, str]]] = None
    # The concrete source "auto" is pinned to, for a backend that can't detect
    # the language itself. LLMs detect from content and leave "auto" alone.
    auto_source: Optional[str] = None
    # Model ids the provider has shut down. `ConfigManager` swaps a saved one
    # for `default_model` as the config loads: `save_settings` writes the
    # defaults into `config.toml`, so every config ever saved still named
    # `gemini-1.5-flash` long after Google retired it (#32).
    retired_models: FrozenSet[str] = field(default_factory=frozenset)
    # The model is a fixed identifier, not a choice (Argos).
    model_is_fixed: bool = False
    signup_url: Optional[str] = None


def _openai_translator() -> type:
    from ..translators.openai_translator import OpenAITranslator

    return OpenAITranslator


def _gemini_translator() -> type:
    from ..translators.gemini_translator import GeminiTranslator

    return GeminiTranslator


def _anthropic_translator() -> type:
    from ..translators.anthropic_translator import AnthropicTranslator

    return AnthropicTranslator


def _argos_translator() -> type:
    from ..translators.argos_translator import ArgosTranslator

    return ArgosTranslator


def _openai_lister() -> ModelLister:
    from .listing import list_openai_models

    return list_openai_models


def _anthropic_lister() -> ModelLister:
    from .listing import list_anthropic_models

    return list_anthropic_models


# In the order `TranslationService` lists them, which is the order of the
# OpenAPI enum and of `/config/options`. Appending keeps both stable.
PROVIDERS: Tuple[ProviderSpec, ...] = (
    ProviderSpec(
        id="openai",
        label="OpenAI",
        short_label="OpenAI",
        protocol="openai",
        default_model="gpt-4.1",
        suggested_models=(
            "gpt-4.1",
            "gpt-5.6-luna",
            "gpt-5.6-terra",
            "gpt-5.6-sol",
            "gpt-6-astra",
        ),
        translator=_openai_translator,
        env_prefix="OPENAI",
        takes_endpoint=True,
        default_base_url="https://api.openai.com/v1",
        lister=_openai_lister,
        default_qps=5.0,
        priority=0,
        signup_url="https://platform.openai.com/api-keys",
    ),
    ProviderSpec(
        id="gemini",
        label="Google Gemini",
        short_label="Gemini",
        protocol="gemini",
        default_model="gemini-3.8-flash",
        suggested_models=(
            "gemini-3.8-flash",
            "gemini-3.7-flash",
            "gemini-3.6-flash",
            "gemini-3.5-flash",
            "gemini-3.5-flash-lite",
        ),
        translator=_gemini_translator,
        env_prefix="GEMINI",
        default_qps=5.0,
        priority=2,
        retired_models=frozenset({
            "gemini-pro",
            "gemini-1.0-pro",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
            "gemini-1.5-flash-8b",
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
        }),
        signup_url="https://aistudio.google.com/apikey",
    ),
    ProviderSpec(
        id="anthropic",
        label="Anthropic Claude",
        short_label="Claude",
        protocol="anthropic",
        default_model="claude-sonnet-4-6",
        suggested_models=(
            "claude-sonnet-4-6",
            "claude-sonnet-5",
            "claude-opus-5",
            "claude-haiku-4-5-20251001",
        ),
        translator=_anthropic_translator,
        env_prefix="ANTHROPIC",
        takes_endpoint=True,
        default_base_url="https://api.anthropic.com",
        lister=_anthropic_lister,
        # Anthropic's entry tier is ~0.83 QPS.
        default_qps=1.0,
        priority=1,
        # From Anthropic's deprecations page.
        retired_models=frozenset({
            "claude-2.0",
            "claude-2.1",
            "claude-3-haiku-20240307",
            "claude-3-sonnet-20240229",
            "claude-3-opus-20240229",
            "claude-3-5-haiku-20241022",
            "claude-3-5-sonnet-20240620",
            "claude-3-5-sonnet-20241022",
            "claude-3-7-sonnet-20250219",
            "claude-sonnet-4-20250514",
            "claude-opus-4-20250514",
            "claude-opus-4-1-20250805",
        }),
        signup_url="https://console.anthropic.com/settings/keys",
    ),
    ProviderSpec(
        id="argos",
        label="Argos Translate (offline)",
        short_label="Argos",
        protocol="argos",
        default_model="argostranslate",
        suggested_models=("argostranslate",),
        translator=_argos_translator,
        requires_key=False,
        supported_pairs=frozenset({("en", "vi")}),
        # Argos has no language detection; English is the dominant source for
        # the academic PDFs this app targets.
        auto_source="en",
        model_is_fixed=True,
    ),
)

_BY_ID = {spec.id: spec for spec in PROVIDERS}


def provider(provider_id: str) -> ProviderSpec:
    """The spec for `provider_id`. `ValueError` for an id no provider has."""
    try:
        return _BY_ID[provider_id]
    except KeyError:
        raise ValueError(
            f"Unknown provider: {provider_id!r}. Known: {list(_BY_ID)}"
        ) from None


def keyed_ids() -> Tuple[str, ...]:
    """Providers that take an API key, in registry order."""
    return tuple(spec.id for spec in PROVIDERS if spec.requires_key)


def endpoint_ids() -> Tuple[str, ...]:
    """Providers that can be pointed at another server, in registry order."""
    return tuple(spec.id for spec in PROVIDERS if spec.takes_endpoint)


def llm_ids_by_priority() -> Tuple[str, ...]:
    """The fallback order for "any LLM with a key"; see `ProviderSpec.priority`."""
    ranked = [spec for spec in PROVIDERS if spec.priority is not None]
    return tuple(spec.id for spec in sorted(ranked, key=lambda spec: spec.priority))
