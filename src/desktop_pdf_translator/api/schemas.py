"""Pydantic request/response schemas for the sidecar API."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..config import LanguageCode, TranslationService


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class APIKeyMaskedSettings(BaseModel):
    """Service config with the API key masked. The frontend never sees real keys."""

    has_key: bool
    model: str
    extra: Dict[str, Any] = Field(default_factory=dict)


class ConfigResponse(BaseModel):
    openai: APIKeyMaskedSettings
    gemini: APIKeyMaskedSettings
    anthropic: APIKeyMaskedSettings
    argos: APIKeyMaskedSettings
    translation: Dict[str, Any]
    rag: Dict[str, Any]
    deep_search: Dict[str, Any]
    gui: Dict[str, Any]
    processing: Dict[str, Any] = Field(default_factory=dict)
    debug_mode: bool


class ServiceCredentialUpdate(BaseModel):
    """Update payload for one service. `api_key=None` means leave unchanged;
    `api_key=""` means clear it."""

    api_key: Optional[str] = None
    model: Optional[str] = None


class ConfigUpdateRequest(BaseModel):
    openai: Optional[ServiceCredentialUpdate] = None
    gemini: Optional[ServiceCredentialUpdate] = None
    anthropic: Optional[ServiceCredentialUpdate] = None
    preferred_service: Optional[TranslationService] = None
    default_source_lang: Optional[LanguageCode] = None
    default_target_lang: Optional[LanguageCode] = None
    rag_enabled: Optional[bool] = None
    # Performance / cache toggles
    max_parallel_chunks: Optional[int] = Field(None, ge=0, le=16)
    cache_translations: Optional[bool] = None


class ValidateRequest(BaseModel):
    service: TranslationService
    api_key: str
    model: Optional[str] = None


class ValidateResponse(BaseModel):
    valid: bool
    message: str


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------


class TranslateRequest(BaseModel):
    file_path: str
    source_lang: LanguageCode = LanguageCode.AUTO
    target_lang: LanguageCode = LanguageCode.VIETNAMESE
    service: Optional[TranslationService] = None
    output_dir: Optional[str] = None
    visible_page: int = Field(1, ge=1, description="1-indexed page the viewer is currently showing; seeds the priority queue")


class JobAccepted(BaseModel):
    job_id: str


class PrewarmRequest(BaseModel):
    """Fire-and-forget request to warm a translator before the user clicks
    Translate. For Argos this triggers the en→vi pack install. For LLMs it
    instantiates the SDK client so the first translate() call avoids cold-start."""

    service: Optional[TranslationService] = None
    source_lang: LanguageCode = LanguageCode.AUTO
    target_lang: LanguageCode = LanguageCode.VIETNAMESE


class PrewarmResponse(BaseModel):
    service: str
    warmed: bool
    cached: bool  # whether this warm-up was already done in-process
    message: str


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class CacheStatsResponse(BaseModel):
    entries: int = 0
    active: int = 0
    expired: int = 0
    size_mb: float = 0.0
    by_service: Dict[str, int] = Field(default_factory=dict)
    hits: int = 0
    misses: int = 0
    hit_rate: float = 0.0
    cache_dir: str = ""
    ttl_days: int = 30
    max_size_mb: float = 500.0


class CacheClearResponse(BaseModel):
    removed: int
    scope: str  # "expired" | "all"


# ---------------------------------------------------------------------------
# RAG
# ---------------------------------------------------------------------------


class IndexRequest(BaseModel):
    file_path: str
    document_id: Optional[str] = None  # defaults to filename


class AskRequest(BaseModel):
    question: str
    document_id: Optional[str] = None
    include_web_research: bool = False
    use_deep_search: bool = False
    max_pdf_sources: int = 5
    max_web_sources: int = 5


# ---------------------------------------------------------------------------
# Languages / services list (helper for the frontend)
# ---------------------------------------------------------------------------


class LanguageOption(BaseModel):
    code: str
    label: str


class ServiceOption(BaseModel):
    code: str
    label: str
    models: List[str]


class OptionsResponse(BaseModel):
    languages: List[LanguageOption]
    services: List[ServiceOption]
