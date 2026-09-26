"""
Configuration models for desktop PDF translator application.
"""

from enum import Enum 
from pathlib import Path
from typing import Annotated, Dict, FrozenSet, List, Optional, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    AfterValidator,
    ValidationInfo,
    field_validator,
    model_validator,
)

from ..providers.registry import PROVIDERS, provider

class LanguageCode(str, Enum):
    """Supported language codes with Vietnamese priority."""
    
    AUTO = "auto"
    VIETNAMESE = "vi"
    ENGLISH = "en"
    JAPANESE = "ja"
    CHINESE_SIMPLIFIED = "zh-cn"
    CHINESE_TRADITIONAL = "zh-tw"

# Built from the provider registry, so a provider is added in one place. The
# member names (`TranslationService.OPENAI`) are the ids upper-cased, and the
# order is the registry's. Internal only: on the wire a provider is a
# `ProviderId` string (below), so the enum never reaches the OpenAPI schema.
TranslationService = Enum(
    "TranslationService",
    [(spec.id.upper(), spec.id) for spec in PROVIDERS],
    type=str,
    module=__name__,
)
TranslationService.__doc__ = "Supported translation services."



def _registry_provider(value: str) -> "TranslationService":
    return TranslationService(value)  # ValueError for an id no provider has


# A provider as the API carries it: its id, a plain string, checked against
# the registry and handed to the code as the enum above. Published as a
# string, not an enum of ids, so a provider added to the registry changes
# neither `openapi.json` nor the generated TypeScript (#88); `GET /providers`
# is where a client learns which ids exist.
ProviderId = Annotated[
    str,
    AfterValidator(_registry_provider),
    Field(description="A provider's id, as GET /providers lists them"),
]


def normalize_base_url(value: Optional[str]) -> Optional[str]:
    """An API endpoint as it is stored: `None` for the provider's own.

    Whitespace and trailing slashes go, so one server typed two ways is one
    endpoint. That matters beyond tidiness: `api/routes/config.py` compares
    endpoints to decide whether a saved key may be sent to one (#32).
    """
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    value = value.rstrip("/")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("must be an http:// or https:// URL")
    return value


# Model IDs their provider has shut down, per service; see
# `ProviderSpec.retired_models`.
RETIRED_MODELS: Dict[str, FrozenSet[str]] = {
    spec.id: spec.retired_models for spec in PROVIDERS if spec.retired_models
}


ModelName = Annotated[str, Field(min_length=1, max_length=200)]


class ModelRef(BaseModel):
    """One model of one provider: what translates, or what answers in chat.

    Any name the provider serves, not only a suggested one (#32). A provider
    whose model is a fixed identifier (Argos) always names that one, so a
    stale or hand-edited name can't reach its cache keys.
    """

    provider: ProviderId
    model: ModelName

    @field_validator("model", mode="before")
    @classmethod
    def _strip(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _pin_fixed_model(self) -> "ModelRef":
        spec = provider(self.provider.value)
        if spec.model_is_fixed:
            self.model = spec.default_model
        return self


class ProviderSettings(BaseModel):
    """One provider's credentials and parameters — the same class for every
    provider, so adding one is a registry entry (#85).

    Which *model* runs is not here: that is `translation.model` and
    `rag.answer_model`. `enabled_models` is the provider's own list, in order;
    its first entry is the model this provider runs when it is chosen without
    one naming it (`AppSettings.model_for`).

    The rules that differ per provider — the temperature ceiling, whether an
    endpoint is taken, the default `max_tokens` — are read from its
    `ProviderSpec` through `provider_id`, which `AppSettings` fills in from the
    map's key. They are field validators rather than one model validator so
    that a bad value fails at `providers.<id>.<field>`: the load path drops
    exactly that field and keeps the provider's key (`ConfigManager.
    _load_with_invalid_fields_dropped`).
    """

    # Filled in from the `providers` map key; never written to `config.toml`.
    # Declared first: the validators below read it from `info.data`.
    provider_id: Optional[str] = Field(None, exclude=True)
    api_key: Optional[str] = Field(None, description="API key")
    base_url: Optional[str] = Field(
        None, description="API endpoint of the user's own. None = the provider's"
    )
    enabled_models: List[ModelName] = Field(
        default_factory=list, description="Models offered for this provider, current first"
    )
    temperature: float = Field(0.3, ge=0.0, le=2.0, description="Translation creativity")
    max_tokens: Optional[int] = Field(None, ge=1, description="Maximum tokens per request")
    max_qps: Optional[float] = Field(
        None, ge=0.1, le=200.0,
        description="Requests/sec cap shared across every concurrent job. None = built-in default",
    )

    @model_validator(mode="before")
    @classmethod
    def _registry_defaults(cls, data):
        # `None` too, not only a missing value: it is how `PUT /providers`
        # says "the default", and Anthropic's API refuses a request without one.
        if isinstance(data, dict) and data.get("max_tokens") is None:
            spec = _spec_or_none(data.get("provider_id"))
            if spec is not None and spec.default_max_tokens is not None:
                data = {**data, "max_tokens": spec.default_max_tokens}
        return data

    @field_validator("provider_id")
    @classmethod
    def _known_provider(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            provider(value)  # ValueError for an id no provider has
        return value

    @field_validator("api_key")
    @classmethod
    def _no_key_for_a_keyless_provider(
        cls, value: Optional[str], info: ValidationInfo
    ) -> Optional[str]:
        # One hand-edited into Ollama's table would be sent in place of its
        # placeholder and could never be cleared: the API refuses a key for
        # it both ways, while the endpoint rule guards any saved key (#88).
        spec = _spec_or_none(info.data.get("provider_id"))
        if spec is not None and not spec.requires_key:
            return None
        return value

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url(cls, value: Optional[str], info: ValidationInfo) -> Optional[str]:
        value = normalize_base_url(value)
        spec = _spec_or_none(info.data.get("provider_id"))
        if value is not None and spec is not None and not spec.takes_endpoint:
            raise ValueError(f"{spec.label} takes no endpoint of its own")
        return value

    @field_validator("enabled_models", mode="before")
    @classmethod
    def _dedupe(cls, value):
        if not isinstance(value, list):
            return value
        names = [v.strip() if isinstance(v, str) else v for v in value]
        return list(dict.fromkeys(names))

    @field_validator("temperature")
    @classmethod
    def _temperature_in_range(cls, value: float, info: ValidationInfo) -> float:
        spec = _spec_or_none(info.data.get("provider_id"))
        if spec is not None and value > spec.max_temperature:
            raise ValueError(
                f"{spec.label} takes a temperature of at most {spec.max_temperature}"
            )
        return value


def _spec_or_none(provider_id):
    try:
        return provider(provider_id) if isinstance(provider_id, str) else None
    except ValueError:
        return None


def _providers_default() -> Dict[str, "ProviderSettings"]:
    return {spec.id: ProviderSettings(provider_id=spec.id) for spec in PROVIDERS}


# Bounds shared with `PUT /config` (`api/schemas.py`), so the API answers 422
# for exactly what this model would refuse. The caps stay where they were
# before #33: the rolling PDF is rewritten after every page, so a run's cost
# grows with the document's length and size, not only with its page count.
MaxPages = Annotated[int, Field(ge=1, le=100)]
MaxFileSizeMB = Annotated[float, Field(ge=1.0, le=200.0)]


class TranslationSettings(BaseModel):
    """Translation-specific settings."""
    
    default_source_lang: LanguageCode = Field(
        LanguageCode.AUTO, 
        description="Default source language"
    )
    default_target_lang: LanguageCode = Field(
        LanguageCode.VIETNAMESE, 
        description="Default target language (Vietnamese priority)"
    )
    # What translates. Argos until a key is saved for an LLM, which is when
    # `PUT /config` moves off it by itself. Replaced `preferred_service` plus
    # the model in each provider's section (#85); see `ConfigManager._convert_v1`.
    model: ModelRef = Field(
        default_factory=lambda: ModelRef(
            provider=TranslationService.ARGOS, model=provider("argos").default_model
        ),
        description="The model that translates",
    )
    # Pages one translation may cover — the selected pages, not the document's
    # length (#33). A longer PDF is translated part by part.
    max_pages: MaxPages = Field(50, description="Maximum pages per translation")
    max_file_size_mb: MaxFileSizeMB = Field(50.0, description="Maximum file size in MB")
    cache_translations: bool = Field(True, description="Enable translation caching")
    cache_ttl_days: int = Field(30, ge=1, le=365, description="Days before cached translations expire")
    cache_max_size_mb: float = Field(500.0, ge=10.0, le=5000.0, description="Soft cap for translation cache size (MB)")
    cache_translated_pdfs: bool = Field(
        True,
        description="Enable PDF-level translation cache (skip re-translating identical PDFs)",
    )
    pdf_cache_max_size_mb: float = Field(
        1000.0, ge=100.0, le=20000.0,
        description="Soft cap for the translated-PDF cache; oldest entries are LRU-evicted past this",
    )
    preserve_formatting: bool = Field(True, description="Preserve PDF formatting")
    min_text_length: int = Field(5, ge=0, description="Minimum text length to translate")

class GUISettings(BaseModel):
    """GUI-specific settings.

    No window geometry here. `tauri-plugin-window-state` persists size,
    position and maximized state itself. The `window_width` / `window_height`
    fields that used to live here were left over from the PySide6 GUI and
    nothing ever read them, so the window opened at `tauri.conf.json`'s
    1400×900 every time regardless. Config files that still carry them load
    fine — pydantic ignores unknown keys by default.
    """

    theme: Literal["light", "dark", "system"] = Field("system", description="UI theme")
    show_advanced_options: bool = Field(False, description="Show advanced translation options")
    auto_preview: bool = Field(True, description="Auto-preview translations")
    vietnamese_font_priority: bool = Field(True, description="Prioritize Vietnamese fonts")

class ProcessingSettings(BaseModel):
    """PDF processing settings."""

    max_workers: int = Field(4, ge=1, le=8, description="Maximum parallel workers")
    max_parallel_chunks: int = Field(
        0, ge=0, le=16,
        description="BabelDOC sub-jobs in flight at once. 0 = auto (cpu // 2, clamped to 2-8)",
    )
    timeout_seconds: int = Field(300, ge=30, le=3600, description="Processing timeout")
    quality_check: bool = Field(True, description="Enable translation quality checks")
    backup_originals: bool = Field(True, description="Keep backup of original files")

class RAGSettings(BaseModel):
    """RAG (Retrieval-Augmented Generation) settings."""

    # `GET /config` returns this as it is, every field included; the flag
    # says so in the generated TypeScript (`api/schemas.py:_EVERY_FIELD_SENT`).
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    # Settings → Chat's "Enable chat". Off removes the Chat button and the panel,
    # so no PDF is indexed. A new name, not the old `enabled`: the toolbar wrote
    # that on every show or hide of the panel and nothing ever read it, so the
    # `false` most configs hold meant "panel closed", never "chat off".
    # Pydantic ignores the old key, and the next save drops it (#32).
    chat_enabled: bool = Field(
        True, description="Show the Chat button and index PDFs for chat"
    )
    auto_process_documents: bool = Field(True, description="Auto-process documents for RAG")
    # `None` answers with the translation model. Either way the answer falls
    # back to any LLM with a key (`rag_chain._answer_model`).
    answer_model: Optional[ModelRef] = Field(
        None, description="The model that answers in chat. None = the translation model"
    )


class AppSettings(BaseModel):
    """Main application settings model."""
    
    # One entry per registry provider, keyed by its (frozen) id.
    providers: Dict[str, ProviderSettings] = Field(default_factory=_providers_default)

    # Application settings
    translation: TranslationSettings = Field(default_factory=TranslationSettings)
    gui: GUISettings = Field(default_factory=GUISettings)
    processing: ProcessingSettings = Field(default_factory=ProcessingSettings)
    rag: RAGSettings = Field(default_factory=RAGSettings)

    # Application metadata
    debug_mode: bool = Field(False, description="Enable debug logging")

    @field_validator("providers", mode="before")
    @classmethod
    def _every_provider(cls, value):
        """Name each entry after its key, and give every registry provider
        one. An id no provider has fails at `providers.<id>.provider_id`."""
        if not isinstance(value, dict):
            return value
        filled = {}
        for provider_id, entry in value.items():
            if isinstance(entry, ProviderSettings):
                entry = entry.model_dump()
            if isinstance(entry, dict):
                entry = {**entry, "provider_id": provider_id}
            filled[provider_id] = entry
        for spec in PROVIDERS:
            filled.setdefault(spec.id, {"provider_id": spec.id})
        return filled

    @field_validator('translation')
    @classmethod
    def validate_translation_settings(cls, v):
        """Validate translation settings for Vietnamese priority."""
        if v.default_target_lang == LanguageCode.AUTO:
            v.default_target_lang = LanguageCode.VIETNAMESE
        return v
    
    def has_api_key(self, service: TranslationService) -> bool:
        """Whether the given service has an API key configured.

        A provider that takes no key (Argos) always counts as having one.
        """
        spec = provider(TranslationService(service).value)
        if not spec.requires_key:
            return True
        return bool(self.providers[spec.id].api_key)

    def model_for(self, provider_id) -> str:
        """The model `provider_id` runs when it is chosen without one named.

        The translation model when it is that provider's; otherwise the first
        of its `enabled_models` — which is where a model picked for it earlier
        went (`remember_model`) — and otherwise its default. The per-service
        `model` of `GET /config` is this, so switching providers and back keeps
        each one's model, as when each section held its own.
        """
        spec = provider(TranslationService(provider_id).value)
        if spec.model_is_fixed:
            return spec.default_model
        if self.translation.model.provider.value == spec.id:
            return self.translation.model.model
        enabled = self.providers[spec.id].enabled_models
        return enabled[0] if enabled else spec.default_model

    def remember_model(self, provider_id, model: str) -> None:
        """Make `model` the one `provider_id` runs by default: first in its
        `enabled_models`, which it joins if it wasn't there. A no-op for a
        provider whose model is fixed."""
        spec = provider(TranslationService(provider_id).value)
        if spec.model_is_fixed:
            return
        entry = self.providers[spec.id]
        entry.enabled_models = [model] + [m for m in entry.enabled_models if m != model]

    def translate_with(self, ref: ModelRef) -> None:
        """Make `ref` the translation model.

        Both models are remembered for their providers: the outgoing one so
        that its provider still reports it once it no longer translates, and
        the new one so that it stays its provider's after a switch away.
        """
        outgoing = self.translation.model
        self.remember_model(outgoing.provider, outgoing.model)
        self.remember_model(ref.provider, ref.model)
        self.translation.model = ModelRef(provider=ref.provider, model=ref.model)

class FileMetadata(BaseModel):
    """Metadata for processed PDF files."""

    original_path: Path
    filename: str
    file_size_mb: float
    page_count: int
    # The pages a translation covers, ascending; `None` for all of them.
    selected_pages: Optional[List[int]] = None