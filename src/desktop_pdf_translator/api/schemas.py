"""Pydantic request/response schemas for the sidecar API."""

from typing import Annotated, Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator

from .sse_schemas import AskResultPayload

from ..config import (
    GUISettings,
    LanguageCode,
    ModelRef,
    ProcessingSettings,
    RAGSettings,
    TranslationService,
    TranslationSettings,
)
from ..config.models import MaxFileSizeMB, MaxPages, normalize_base_url


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _endpoint_or_blank(value: Optional[str]) -> Optional[str]:
    """`None` stays `None`, blank is `""` (the provider's own endpoint), and
    anything else has to be an http(s) URL, stored the way settings store it."""
    if value is None:
        return None
    if not value.strip():
        return ""
    return normalize_base_url(value)


class APIKeyMaskedSettings(BaseModel):
    """Service config with the API key masked. The frontend never sees real keys."""

    has_key: bool
    model: str
    # The server this service talks to when it isn't the provider's own
    # (OpenAI and Anthropic only). Not a secret, unlike the key beside it.
    base_url: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


# A response always carries every field, defaulted or not. Without this the
# generated TypeScript marks each defaulted one optional, and the frontend
# guards values that are always there.
_EVERY_FIELD_SENT = ConfigDict(json_schema_serialization_defaults_required=True)


class TranslationConfig(TranslationSettings):
    """`[translation]` as the frontend reads it: the settings, plus
    `preferred_service` — `model.provider` — for a client older than #86."""

    model_config = _EVERY_FIELD_SENT

    preferred_service: TranslationService


class ConfigResponse(BaseModel):
    # One block per provider, as before #85, for the frontend until #88: the
    # key's presence, the endpoint, and the model it runs when chosen
    # (`AppSettings.model_for`).
    openai: APIKeyMaskedSettings
    gemini: APIKeyMaskedSettings
    anthropic: APIKeyMaskedSettings
    argos: APIKeyMaskedSettings
    # Real nested settings models, not Dict[str, Any] — the latter would erase
    # exactly the fields (default_source_lang, default_target_lang,
    # preferred_service, ...) whose drift from the hand-written frontend types
    # is what issue #27 is about.
    translation: TranslationConfig
    rag: RAGSettings
    gui: GUISettings
    processing: ProcessingSettings
    debug_mode: bool


class ServiceCredentialUpdate(BaseModel):
    """Update payload for one service. `api_key=None` means leave unchanged;
    `api_key=""` means clear it. `model` is any name the provider serves, not
    only one `/config/options` suggests."""

    api_key: Optional[str] = None
    model: Optional[str] = Field(None, max_length=200)

    @field_validator("model")
    @classmethod
    def _model_is_named(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class EndpointCredentialUpdate(ServiceCredentialUpdate):
    """For a service that can talk to another server speaking its API: Ollama,
    LM Studio, a proxy (#32).

    `base_url=None` leaves the endpoint unchanged and `""` returns to the
    provider's own. Changing it while a key is saved needs the key in the same
    update, because a saved key is only ever sent to the endpoint it was saved
    for (`routes/config.py:update_config`).
    """

    base_url: Optional[str] = None

    @field_validator("base_url")
    @classmethod
    def _endpoint(cls, value: Optional[str]) -> Optional[str]:
        return _endpoint_or_blank(value)


class ConfigUpdateRequest(BaseModel):
    # The per-provider blocks and `preferred_service` are the shape from
    # before #85, kept for the frontend until #88. A block's `model` becomes
    # the model that provider runs; `preferred_service` makes that provider's
    # model the translation model. `PUT /providers/{id}` is the new way to
    # set a key, and `translation_model` the new way to choose.
    openai: Optional[EndpointCredentialUpdate] = None
    gemini: Optional[ServiceCredentialUpdate] = None
    anthropic: Optional[EndpointCredentialUpdate] = None
    preferred_service: Optional[TranslationService] = None
    # Wins over `preferred_service` and the blocks' models in the same body.
    translation_model: Optional[ModelRef] = None
    # Left out: unchanged. `null`: answer with the translation model.
    answer_model: Optional[ModelRef] = None
    default_source_lang: Optional[LanguageCode] = None
    default_target_lang: Optional[LanguageCode] = None
    chat_enabled: Optional[bool] = None
    # Performance / cache toggles
    max_parallel_chunks: Optional[int] = Field(None, ge=0, le=16)
    max_pages: Optional[MaxPages] = None
    max_file_size_mb: Optional[MaxFileSizeMB] = None
    cache_translations: Optional[bool] = None
    cache_translated_pdfs: Optional[bool] = None


class ValidateRequest(BaseModel):
    """What to check against the provider. Whatever is left out comes from the
    saved settings, and the saved key is only checked against the saved
    endpoint: naming another one needs the key typed alongside it."""

    service: TranslationService
    # `None` or `""`: the saved key.
    api_key: Optional[str] = None
    # `None` or blank: the saved model.
    model: Optional[str] = Field(None, max_length=200)
    # `None`: the saved endpoint. `""`: the provider's own.
    base_url: Optional[str] = None

    @field_validator("model")
    @classmethod
    def _model_or_saved(cls, value: Optional[str]) -> Optional[str]:
        return (value or "").strip() or None

    @field_validator("base_url")
    @classmethod
    def _endpoint(cls, value: Optional[str]) -> Optional[str]:
        return _endpoint_or_blank(value)


class ValidateResponse(BaseModel):
    valid: bool
    message: str


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------


# `[first, last]`, 1-indexed and inclusive. Pages past the end of the document
# are refused by the pre-flight, which is the first place that knows its length.
PageRangeList = Annotated[
    List[Tuple[PositiveInt, PositiveInt]], Field(min_length=1, max_length=500)
]


class TranslateRequest(BaseModel):
    file_path: str
    # `None` means "unspecified" — the configured default applies. These were
    # previously non-null sentinels (AUTO / VIETNAMESE), which made the config
    # fallback in `processor.process_pdf` dead code and pinned every run to
    # Vietnamese no matter what the toolbar said (issue #12). Defaults are
    # resolved in exactly one place: `translators.capabilities.resolve_languages`.
    source_lang: Optional[LanguageCode] = None
    target_lang: Optional[LanguageCode] = None
    service: Optional[TranslationService] = None
    # No `output_dir`. It let a caller name any writable path on the machine,
    # was never sent by the UI, and — being outside `%TEMP%\pdfusion-translate-*`
    # — was exempt from every cleanup path, so it also leaked whatever it
    # wrote. `process_pdf` doesn't take one either: the per-job temp dir is the
    # only answer, so there is nothing left to honour verbatim.
    visible_page: int = Field(1, ge=1, description="1-indexed page the viewer is currently showing; seeds the priority queue")
    bypass_cache: bool = Field(
        False,
        description="Skip the PDF-level cache lookup for this run (forces a fresh translation)",
    )
    page_ranges: Optional[PageRangeList] = Field(
        None,
        description=(
            "Pages to translate, as [first, last] ranges (1-indexed, inclusive). "
            "Omitted or null: the whole document. The other pages stay in the "
            "output untranslated, and `max_pages` limits the pages selected."
        ),
    )


class EstimateRequest(BaseModel):
    """What a translation of these pages would cost an LLM, before it runs."""

    file_path: str
    page_ranges: Optional[PageRangeList] = Field(
        None, description="As in TranslateRequest; omitted or null: every page"
    )
    # `None`: the configured default, as for /translate.
    target_lang: Optional[LanguageCode] = None


class TranslationEstimate(BaseModel):
    """A rough count (`translators/usage_estimate.py`): PyMuPDF's text blocks
    stand in for BabelDOC's paragraphs, and characters for tokens."""

    page_count: int
    pages_selected: int
    paragraphs: int
    input_tokens: int
    output_tokens: int


class JobAccepted(BaseModel):
    job_id: str


class PrewarmRequest(BaseModel):
    """Fire-and-forget request to warm a translator before the user clicks
    Translate. For Argos this triggers the en→vi pack install. For LLMs it
    instantiates the SDK client so the first translate() call avoids cold-start."""

    service: Optional[TranslationService] = None
    # Same "None means unspecified" contract as TranslateRequest — pre-warming
    # auto→vi while the toolbar says Japanese warms the wrong backend.
    source_lang: Optional[LanguageCode] = None
    target_lang: Optional[LanguageCode] = None


class PrewarmResponse(BaseModel):
    service: str
    warmed: bool
    cached: bool  # whether this warm-up was already done in-process
    message: str


# ---------------------------------------------------------------------------
# PDF export
# ---------------------------------------------------------------------------


class ExportPdfRequest(BaseModel):
    """Save a translated PDF to a permanent location the user picked.

    `source_path` is whatever the translation job reported (a rolling file in
    the per-job temp dir, or a cache materialization); `destination_path` comes
    straight from the native Save dialog.
    """

    source_path: str
    destination_path: str
    # The document the user opened. Never a valid destination: overwriting it
    # with the translation destroys their input, and the suggested filename can
    # collide with it for an already-`_vi`-suffixed source.
    protect_path: Optional[str] = None


class ExportPdfResponse(BaseModel):
    saved_path: str
    bytes_written: int


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class CacheStatsResponse(BaseModel):
    """The paragraph cache (`translators/translation_cache.py`)."""

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


class PdfCacheStatsResponse(BaseModel):
    """The whole-PDF cache (`processors/pdf_cache.py`)."""

    entries: int = 0
    size_mb: float = 0.0
    # Past this, the least recently used PDFs are evicted.
    max_size_mb: float = 1000.0
    by_service: Dict[str, int] = Field(default_factory=dict)
    hits: int = 0
    misses: int = 0
    hit_rate: float = 0.0
    cache_dir: str = ""


class CacheOverviewResponse(BaseModel):
    """Both translation caches, which Settings → Cache shows side by side. It
    used to show only the paragraph cache, while its "Clear all" emptied both
    (#32)."""

    paragraph: CacheStatsResponse
    pdf: PdfCacheStatsResponse


class CacheClearResponse(BaseModel):
    removed: int
    scope: Literal["expired", "all"]
    target: Literal["paragraph", "pdf", "all"]


# ---------------------------------------------------------------------------
# Offline engine setup
# ---------------------------------------------------------------------------


class EngineAssetGroup(BaseModel):
    id: str
    label: str
    ready: bool
    present: int
    total: int
    detail: str


class EngineInstallState(BaseModel):
    """The install that `POST /setup/engine` starts, as `GET /setup/status`
    reports it. Polled rather than streamed — see `api/routes/setup.py`."""

    running: bool
    stage: Optional[str] = None
    # The last install's failure, kept after it finishes so a client that was
    # not watching still finds out why nothing was installed.
    error: Optional[str] = None


class EngineStatusResponse(BaseModel):
    ready: bool
    groups: List[EngineAssetGroup]
    # True when the installer shipped the assets, so setup is a local unzip
    # rather than a download. The setup screen words its button from this.
    bundled: bool
    install: EngineInstallState


# ---------------------------------------------------------------------------
# RAG
# ---------------------------------------------------------------------------


class IndexRequest(BaseModel):
    # No `document_id`: the sidecar derives it from the file's bytes. It used
    # to default to the file name stem, so two different `paper.pdf`s shared
    # one index and chat answered about the second from the first (#59).
    file_path: str


class AskRequest(BaseModel):
    question: str
    # Required. `None` used to mean "every indexed document", which is how chat
    # came to answer from PDFs other than the open one (#59).
    document_id: str = Field(min_length=1)
    max_pdf_sources: int = 5
    # The language to answer in: the toolbar's "To" language. `None` means the
    # configured default, the rule `/translate` follows too. Answers used to be
    # Vietnamese whatever was chosen (#31).
    target_lang: Optional[LanguageCode] = None


class DocumentSummaryResponse(BaseModel):
    """A recorded document, as Settings → Chat lists it (#31). Metadata only."""

    document_id: str
    display_name: str
    # Where it was most recently opened from.
    path: Optional[str] = None
    size_bytes: int
    page_count: Optional[int] = None
    # Chunks in the ready index a question would use; `None` without one.
    chunk_count: Optional[int] = None
    question_count: int
    last_opened_at: str  # ISO-8601 with a UTC offset


class DocumentListResponse(BaseModel):
    documents: List[DocumentSummaryResponse]


class ChatMessageResponse(BaseModel):
    """One saved chat message (#31)."""

    id: int
    role: Literal["user", "assistant"]
    text: str
    # Assistant messages only: the answer as it was sent, citations included.
    # Its pages still point at the right place, because a document's id is the
    # hash of its bytes; its `chunk_id`s name chunks of an index that may be gone.
    answer: Optional[AskResultPayload] = None
    created_at: str  # ISO-8601 with a UTC offset
    # Assistant messages only: the provider and model that wrote the answer.
    # `None` for an answer no model wrote, and for one saved before #87.
    provider: Optional[str] = None
    model: Optional[str] = None


class ChatHistoryResponse(BaseModel):
    messages: List[ChatMessageResponse]


class ResetIndexesResponse(BaseModel):
    # How many chat indexes were deleted.
    removed: int


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
    # Every (source, target) pair this backend can produce, or `None` for
    # "unrestricted". Lets the toolbar grey out targets the selected backend
    # can't reach instead of letting the user start a job that dies mid-run.
    # Auto-source aliases are already expanded server-side.
    supported_pairs: Optional[List[List[str]]] = None


class OptionsResponse(BaseModel):
    languages: List[LanguageOption]
    services: List[ServiceOption]


class EndpointModelsResponse(BaseModel):
    """The models a service's saved endpoint says it serves.

    A failure is `error`, not an HTTP error: a local server that isn't running
    yet is ordinary, and the picker still offers the saved model without it.
    """

    models: List[str] = Field(default_factory=list)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Providers (#84)
# ---------------------------------------------------------------------------

# `unverified`: never checked, or nothing to check with. `unreadable`: a key is
# saved but this process could not decrypt it (a locked keyring).
KeyState = Literal["unverified", "valid", "invalid", "unreadable"]


class ModelRecord(BaseModel):
    id: str
    display_name: Optional[str] = None
    context_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    created_at: Optional[str] = None
    # `listed`: the endpoint said it serves it. `saved`: the configured model,
    # kept on offer when no list has it, so a name typed by hand never
    # disappears. `suggested`: the registry's suggestions, standing in when
    # there is no list for the provider's own endpoint.
    source: Literal["listed", "saved", "suggested"]


class ProviderInfo(BaseModel):
    model_config = _EVERY_FIELD_SENT

    id: TranslationService
    label: str
    short_label: str
    description: str
    protocol: Literal["openai", "anthropic", "gemini", "argos"]
    requires_key: bool
    takes_endpoint: bool
    default_base_url: Optional[str] = None
    endpoint_hint: Optional[str] = None
    default_model: str
    suggested_models: List[str]
    model_is_fixed: bool
    signup_url: Optional[str] = None
    # Its place in chat's "any LLM with a key" fallback, lowest first; `None`
    # for one that never answers in chat. The frontend's copy of that order
    # (`lib/model-choice.ts:chatModel`) reads it here.
    priority: Optional[int] = None
    has_key: bool
    base_url: Optional[str] = None
    key_state: KeyState
    last_verified_at: Optional[str] = None
    catalog_fetched_at: Optional[str] = None
    catalog_fresh: bool = False
    # The saved settings beside the key (#85). `model` is the one it runs
    # when chosen without one named: `enabled_models[0]`, or the translation
    # model when this provider translates, or its default.
    model: str
    enabled_models: List[str] = Field(default_factory=list)
    temperature: float
    max_tokens: Optional[int] = None
    max_qps: Optional[float] = None


class ProviderUpdateRequest(BaseModel):
    """One provider's key, endpoint, models and parameters. Left out (or
    `null`), each is unchanged.

    `api_key=""` clears the key. `base_url=""` returns to the provider's own
    endpoint; changing it while a key is saved needs `api_key` in the same
    body, the rule `PUT /config` keeps (#32). Bounds that differ per provider
    — the temperature ceiling, whether it takes an endpoint at all — are
    checked against the registry, and a 422 names the one broken.
    """

    api_key: Optional[str] = None
    base_url: Optional[str] = None
    enabled_models: Optional[List[Annotated[str, Field(max_length=200)]]] = Field(
        None, max_length=500
    )
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(None, ge=1)
    max_qps: Optional[float] = Field(None, ge=0.1, le=200.0)

    @field_validator("base_url")
    @classmethod
    def _endpoint(cls, value: Optional[str]) -> Optional[str]:
        return _endpoint_or_blank(value)

    @field_validator("enabled_models")
    @classmethod
    def _models_are_named(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is None:
            return None
        names = [name.strip() for name in value]
        if not all(names):
            raise ValueError("a model name must not be blank")
        return list(dict.fromkeys(names))


class ProvidersResponse(BaseModel):
    providers: List[ProviderInfo]


class ModelCatalogResponse(BaseModel):
    """The models the saved key can use at the saved endpoint.

    A failure is `error` in a 200, as with `EndpointModelsResponse`: a local
    server that isn't up yet is ordinary, and `models` still carries the saved
    model (and, on the provider's own endpoint, the suggestions).
    """

    model_config = _EVERY_FIELD_SENT

    models: List[ModelRecord] = Field(default_factory=list)
    # What the non-chat id filter took out, for a "show all".
    hidden: List[ModelRecord] = Field(default_factory=list)
    fetched_at: Optional[str] = None
    key_state: KeyState = "unverified"
    error: Optional[str] = None


class VerifyRequest(BaseModel):
    """A key and endpoint to check by listing, saving nothing. Left out, each
    comes from the saved settings — but the saved key is only ever sent to the
    saved endpoint."""

    # `None` or `""`: the saved key.
    api_key: Optional[str] = None
    # `None`: the saved endpoint. `""`: the provider's own.
    base_url: Optional[str] = None
    # Checked against the list when given; `None` or blank: not checked.
    model: Optional[str] = Field(None, max_length=200)

    @field_validator("model")
    @classmethod
    def _model_or_none(cls, value: Optional[str]) -> Optional[str]:
        return (value or "").strip() or None

    @field_validator("base_url")
    @classmethod
    def _endpoint(cls, value: Optional[str]) -> Optional[str]:
        return _endpoint_or_blank(value)


class VerifyResponse(BaseModel):
    model_config = _EVERY_FIELD_SENT

    # The key works at that endpoint: the listing succeeded.
    valid: bool
    key_state: KeyState
    message: str
    # Whether `model` is among the listed ids, aliases allowed; `None` when no
    # model was given or nothing was listed.
    model_found: Optional[bool] = None
    models: List[ModelRecord] = Field(default_factory=list)
    hidden: List[ModelRecord] = Field(default_factory=list)
