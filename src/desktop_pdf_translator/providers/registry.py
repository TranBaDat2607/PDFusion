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
from typing import TYPE_CHECKING, Callable, FrozenSet, Literal, Optional, Tuple

if TYPE_CHECKING:
    from .listing import Listing

Protocol = Literal["openai", "anthropic", "gemini", "argos"]
ModelLister = Callable[[str, Optional[str]], "Listing"]


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    # For narrow places — a toolbar button, a chat header — where `label` is
    # too wide.
    short_label: str
    # One line under the name on the Models page's card (#86).
    description: str
    # The API it speaks. Nothing dispatches on it yet — each spec names its own
    # `translator` and `lister` — but it is what will let a provider that
    # speaks OpenAI's API share OpenAI's translator (#88).
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
    # Where requests go with no endpoint of the user's own. Sent to the SDK
    # (`endpoint_for`), not left to its default: that is what lets OpenRouter
    # reach its own URL through OpenAI's SDK (#88), and it keeps an
    # `OPENAI_BASE_URL` in the user's environment from redirecting a saved
    # key. Also the Models page's placeholder (#86).
    default_base_url: Optional[str] = None
    # Under the Models page's "Override base URL": what a local server's
    # endpoint looks like. Required when `takes_endpoint`.
    endpoint_hint: Optional[str] = None
    # Returns `list(api_key, base_url) -> Listing`: the models a key can use.
    # It is also how a key is verified — never by generating text (#84) — so
    # every keyed provider has one. Imports on call.
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
    # What to send as the key to a server that takes none (`requires_key`
    # False) but whose SDK won't build a client without one: OpenAI's refuses
    # an empty key, and Ollama ignores whatever it is sent (#88).
    placeholder_key: Optional[str] = None
    # Where to get a key, linked from the Models page (#86).
    signup_url: Optional[str] = None
    # The highest `temperature` its API takes. OpenAI's scale runs to 2; the
    # others refuse anything above 1 with a 400 on every paragraph.
    max_temperature: float = 1.0
    # `max_tokens` a fresh config carries. `None` leaves it to the translator:
    # OpenAI's reasoning models refuse the parameter outright, while
    # Anthropic's API requires one on every request.
    default_max_tokens: Optional[int] = None

    @property
    def is_llm(self) -> bool:
        """Writes text from a prompt: it translates by prompting, and it can
        answer in chat. Argos is a translation model and does neither."""
        return self.protocol != "argos"


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


def _gemini_lister() -> ModelLister:
    from .listing import list_gemini_models

    return list_gemini_models


# In the order `TranslationService` lists them, which is the order of the
# OpenAPI enum and of `/config/options`. Appending keeps both stable.
PROVIDERS: Tuple[ProviderSpec, ...] = (
    ProviderSpec(
        id="openai",
        label="OpenAI",
        short_label="OpenAI",
        description="GPT models, or any server that speaks OpenAI's API.",
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
        endpoint_hint=(
            "Leave blank for OpenAI. For a local model, use Ollama at "
            "http://localhost:11434/v1 or LM Studio at http://localhost:1234/v1, "
            "with any API key."
        ),
        lister=_openai_lister,
        default_qps=5.0,
        priority=0,
        signup_url="https://platform.openai.com/api-keys",
        max_temperature=2.0,
    ),
    ProviderSpec(
        id="gemini",
        label="Google Gemini",
        short_label="Gemini",
        description="Google's Gemini models, through the Gemini API.",
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
        lister=_gemini_lister,
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
        description="Anthropic's Claude models.",
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
        endpoint_hint=(
            "Leave blank for Anthropic. For a local model, use Ollama at "
            "http://localhost:11434, with any API key."
        ),
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
        default_max_tokens=4000,
    ),
    ProviderSpec(
        id="argos",
        label="Argos Translate (offline)",
        short_label="Argos",
        description=(
            "Runs on this computer: no key, no usage fees, and nothing leaves "
            "it. Used when no other provider has a key."
        ),
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
    ProviderSpec(
        id="openrouter",
        label="OpenRouter",
        short_label="OpenRouter",
        description="One key for hundreds of models from many labs, through OpenAI's API.",
        # OpenAI's API at its own URL, so OpenAI's translator and lister.
        protocol="openai",
        default_model="openai/gpt-4.1",
        suggested_models=(
            "openai/gpt-4.1",
            "anthropic/claude-sonnet-4.6",
            "google/gemini-3.8-flash",
        ),
        translator=_openai_translator,
        env_prefix="OPENROUTER",
        default_base_url="https://openrouter.ai/api/v1",
        lister=_openai_lister,
        default_qps=5.0,
        priority=3,
        signup_url="https://openrouter.ai/settings/keys",
        max_temperature=2.0,
    ),
)

_BY_ID = {spec.id: spec for spec in PROVIDERS}


def endpoint_for(spec: ProviderSpec, base_url: Optional[str]) -> Optional[str]:
    """Where a request goes: the saved endpoint, or the provider's own.

    The two places that hand an SDK its endpoint — the translator factory and
    `catalog.list_models` — call this; everything else, the key rules and the
    catalog's rows included, keeps the *saved* `base_url`, `None` meaning the
    provider's own.
    """
    return base_url if base_url is not None else spec.default_base_url


def request_key(spec: ProviderSpec, api_key: Optional[str]) -> Optional[str]:
    """The key a request carries: the saved one, or — for a server that takes
    none — its placeholder. A keyed provider with no key gets `None`, never a
    placeholder: that is how it falls back to Argos rather than failing."""
    if api_key:
        return api_key
    return spec.placeholder_key if not spec.requires_key else None


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
    # `_BY_ID`, not `PROVIDERS`: the same entries, but the one table
    # `provider()` reads, so every lookup sees the same spec.
    ranked = [spec for spec in _BY_ID.values() if spec.priority is not None]
    return tuple(spec.id for spec in sorted(ranked, key=lambda spec: spec.priority))
