"""
Configuration models for desktop PDF translator application.
"""

from enum import Enum 
from pathlib import Path
from typing import Annotated, Dict, FrozenSet, List, Optional, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

class LanguageCode(str, Enum):
    """Supported language codes with Vietnamese priority."""
    
    AUTO = "auto"
    VIETNAMESE = "vi"
    ENGLISH = "en"
    JAPANESE = "ja"
    CHINESE_SIMPLIFIED = "zh-cn"
    CHINESE_TRADITIONAL = "zh-tw"

class TranslationService(str, Enum):
    """Supported translation services."""

    OPENAI = "openai"
    GEMINI = "gemini"
    ANTHROPIC = "anthropic"
    ARGOS = "argos"


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


# Model IDs their provider has shut down, per service. A request naming one
# fails on every paragraph, so `ConfigManager.load_settings` swaps a saved one
# for the service's default. The saved copy is the problem: `save_settings`
# writes the defaults into `config.toml`, so every config ever saved still named
# `gemini-1.5-flash` long after Google retired it, and changing the default
# alone reached none of them (#32). The Claude IDs are from Anthropic's
# deprecations page.
RETIRED_MODELS: Dict[str, FrozenSet[str]] = {
    "gemini": frozenset({
        "gemini-pro",
        "gemini-1.0-pro",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
        "gemini-1.5-flash-8b",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
    }),
    "anthropic": frozenset({
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
}


class OpenAISettings(BaseModel):
    """OpenAI translation service settings."""
    
    api_key: Optional[str] = Field(None, description="OpenAI API key")
    model: str = Field("gpt-4.1", description="OpenAI model to use")
    base_url: Optional[str] = Field(
        None, description="OpenAI-compatible API endpoint. None = api.openai.com"
    )
    temperature: float = Field(0.3, ge=0.0, le=2.0, description="Translation creativity")
    max_tokens: Optional[int] = Field(None, description="Maximum tokens per request")
    max_qps: Optional[float] = Field(
        None, ge=0.1, le=200.0,
        description="Requests/sec cap shared across every concurrent job. None = built-in default",
    )

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url(cls, value: Optional[str]) -> Optional[str]:
        return normalize_base_url(value)


class GeminiSettings(BaseModel):
    """Google Gemini translation service settings."""
    
    api_key: Optional[str] = Field(None, description="Google AI API key")
    model: str = Field("gemini-3.8-flash", description="Gemini model to use")
    temperature: float = Field(0.3, ge=0.0, le=1.0, description="Translation creativity")
    max_qps: Optional[float] = Field(
        None, ge=0.1, le=200.0,
        description="Requests/sec cap shared across every concurrent job. None = built-in default",
    )


class AnthropicSettings(BaseModel):
    """Anthropic (Claude) translation service settings."""

    api_key: Optional[str] = Field(None, description="Anthropic API key")
    model: str = Field("claude-sonnet-4-6", description="Anthropic model to use")
    base_url: Optional[str] = Field(
        None, description="Anthropic-compatible API endpoint. None = api.anthropic.com"
    )
    temperature: float = Field(0.3, ge=0.0, le=1.0, description="Translation creativity")
    max_tokens: int = Field(4000, ge=1, description="Maximum tokens per request")
    max_qps: Optional[float] = Field(
        None, ge=0.1, le=200.0,
        description="Requests/sec cap shared across every concurrent job. None = built-in default",
    )

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url(cls, value: Optional[str]) -> Optional[str]:
        return normalize_base_url(value)


class ArgosSettings(BaseModel):
    """Argos Translate (offline NMT) settings.

    Argos has no API key and a single fixed "model" identifier. Kept here so the
    frontend service-tab metadata stays uniform across all backends.
    """

    model: str = Field("argostranslate", description="Argos identifier (fixed)")


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
    preferred_service: TranslationService = Field(
        TranslationService.ARGOS,
        description="Preferred translation service"
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

    # Settings → Chat's "Enable chat". Off removes the Chat button and the panel,
    # so no PDF is indexed. A new name, not the old `enabled`: the toolbar wrote
    # that on every show or hide of the panel and nothing ever read it, so the
    # `false` most configs hold meant "panel closed", never "chat off".
    # Pydantic ignores the old key, and the next save drops it (#32).
    chat_enabled: bool = Field(
        True, description="Show the Chat button and index PDFs for chat"
    )
    auto_process_documents: bool = Field(True, description="Auto-process documents for RAG")


class AppSettings(BaseModel):
    """Main application settings model."""
    
    # Service configurations
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)
    anthropic: AnthropicSettings = Field(default_factory=AnthropicSettings)
    argos: ArgosSettings = Field(default_factory=ArgosSettings)
    
    # Application settings
    translation: TranslationSettings = Field(default_factory=TranslationSettings)
    gui: GUISettings = Field(default_factory=GUISettings)
    processing: ProcessingSettings = Field(default_factory=ProcessingSettings)
    rag: RAGSettings = Field(default_factory=RAGSettings)

    # Application metadata
    debug_mode: bool = Field(False, description="Enable debug logging")

    @field_validator('translation')
    @classmethod
    def validate_translation_settings(cls, v):
        """Validate translation settings for Vietnamese priority."""
        if v.default_target_lang == LanguageCode.AUTO:
            v.default_target_lang = LanguageCode.VIETNAMESE
        return v
    
    def get_active_service_config(self) -> dict:
        """Get configuration for the active translation service."""
        if self.translation.preferred_service == TranslationService.OPENAI:
            return {
                "service": "openai",
                "config": self.openai.model_dump()
            }
        elif self.translation.preferred_service == TranslationService.GEMINI:
            return {
                "service": "gemini",
                "config": self.gemini.model_dump()
            }
        elif self.translation.preferred_service == TranslationService.ANTHROPIC:
            return {
                "service": "anthropic",
                "config": self.anthropic.model_dump()
            }
        elif self.translation.preferred_service == TranslationService.ARGOS:
            return {
                "service": "argos",
                "config": self.argos.model_dump()
            }
        else:
            raise ValueError(f"Unsupported service: {self.translation.preferred_service}")

    def validate_service_credentials(self) -> tuple[bool, str]:
        """Validate that required service credentials are available."""
        active_service = self.get_active_service_config()
        service_name = active_service["service"]
        config = active_service["config"]

        if service_name == "argos":
            return True, "Argos is offline; no credentials required"

        if not config.get("api_key"):
            return False, f"Missing API key for {service_name}"

        return True, "Credentials validated"

    def has_api_key(self, service: TranslationService) -> bool:
        """Whether the given service has an API key configured.

        Argos has no key requirement and always returns True.
        """
        if service == TranslationService.ARGOS:
            return True
        per_service = {
            TranslationService.OPENAI: self.openai.api_key,
            TranslationService.GEMINI: self.gemini.api_key,
            TranslationService.ANTHROPIC: self.anthropic.api_key,
        }
        return bool(per_service.get(service))

class FileMetadata(BaseModel):
    """Metadata for processed PDF files."""

    original_path: Path
    filename: str
    file_size_mb: float
    page_count: int
    # The pages a translation covers, ascending; `None` for all of them.
    selected_pages: Optional[List[int]] = None