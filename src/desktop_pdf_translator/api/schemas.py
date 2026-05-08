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


class JobAccepted(BaseModel):
    job_id: str


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
