"""Configuration + API key management endpoints."""

import logging
from fastapi import APIRouter, Depends, HTTPException

from ...config import (
    AppSettings,
    LanguageCode,
    TranslationService,
    get_config_manager,
    get_settings,
)
from ...translators import TranslatorFactory, get_translation_cache
from ..auth import require_token
from ..schemas import (
    APIKeyMaskedSettings,
    CacheClearResponse,
    CacheStatsResponse,
    ConfigResponse,
    ConfigUpdateRequest,
    OptionsResponse,
    LanguageOption,
    ServiceOption,
    ValidateRequest,
    ValidateResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config", tags=["config"], dependencies=[Depends(require_token)])


def _mask(service_settings) -> APIKeyMaskedSettings:
    # ArgosSettings has no api_key attribute, so getattr falls through to False.
    return APIKeyMaskedSettings(
        has_key=bool(getattr(service_settings, "api_key", None)),
        model=service_settings.model,
        extra={},
    )


@router.get("", response_model=ConfigResponse)
async def get_config() -> ConfigResponse:
    s = get_settings()
    return ConfigResponse(
        openai=_mask(s.openai),
        gemini=_mask(s.gemini),
        anthropic=_mask(s.anthropic),
        argos=_mask(s.argos),
        translation=s.translation.dict(),
        rag=s.rag.dict(),
        deep_search=s.deep_search.dict(),
        gui=s.gui.dict(),
        processing=s.processing.dict(),
        debug_mode=s.debug_mode,
    )


@router.put("", response_model=ConfigResponse)
async def update_config(payload: ConfigUpdateRequest) -> ConfigResponse:
    mgr = get_config_manager()
    current = mgr.settings.dict()

    # Track which LLM services received a non-empty key in *this* PUT, so we
    # can auto-promote the user's preferred_service from Argos to that LLM
    # (priority: openai > anthropic > gemini if multiple keys arrive at once).
    LLM_SERVICES = (
        TranslationService.OPENAI,
        TranslationService.GEMINI,
        TranslationService.ANTHROPIC,
    )
    newly_keyed: list[TranslationService] = []

    for service in LLM_SERVICES:
        update = getattr(payload, service.value)
        if update is None:
            continue
        if update.api_key is not None:
            new_key = update.api_key or None
            current[service.value]["api_key"] = new_key
            if new_key:
                newly_keyed.append(service)
        if update.model is not None:
            current[service.value]["model"] = update.model

    if payload.preferred_service is not None:
        current["translation"]["preferred_service"] = payload.preferred_service.value
    elif (
        current["translation"].get("preferred_service") == TranslationService.ARGOS.value
        and newly_keyed
    ):
        priority = (
            TranslationService.OPENAI,
            TranslationService.ANTHROPIC,
            TranslationService.GEMINI,
        )
        chosen = next((s for s in priority if s in newly_keyed), newly_keyed[0])
        current["translation"]["preferred_service"] = chosen.value
        logger.info(
            "Auto-switching preferred_service argos -> %s after key save",
            chosen.value,
        )

    if payload.default_source_lang is not None:
        current["translation"]["default_source_lang"] = payload.default_source_lang.value
    if payload.default_target_lang is not None:
        current["translation"]["default_target_lang"] = payload.default_target_lang.value
    if payload.rag_enabled is not None:
        current["rag"]["enabled"] = payload.rag_enabled
    if payload.max_parallel_chunks is not None:
        current["processing"]["max_parallel_chunks"] = payload.max_parallel_chunks
    if payload.cache_translations is not None:
        current["translation"]["cache_translations"] = payload.cache_translations

    new_settings = AppSettings(**current)
    if not mgr.save_settings(new_settings):
        raise HTTPException(status_code=500, detail="Failed to save settings")
    mgr._settings = new_settings  # refresh cached singleton
    return await get_config()


@router.post("/validate", response_model=ValidateResponse)
async def validate_credentials(payload: ValidateRequest) -> ValidateResponse:
    """Spin up a translator instance with the supplied credentials and validate."""
    # Argos has no API key — short-circuit and report the install state.
    if payload.service == TranslationService.ARGOS:
        try:
            translator = TranslatorFactory.create_translator(
                service=TranslationService.ARGOS,
                lang_in="en",
                lang_out="vi",
            )
            is_valid, message = translator.validate_configuration()
            return ValidateResponse(valid=is_valid, message=message)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Argos validation failed")
            return ValidateResponse(valid=False, message=str(exc))

    try:
        kwargs = {"api_key": payload.api_key}
        if payload.model:
            kwargs["model"] = payload.model
        translator = TranslatorFactory.create_translator(
            service=payload.service,
            lang_in="en",
            lang_out="vi",
            **kwargs,
        )
        is_valid, message = translator.validate_configuration()
        return ValidateResponse(valid=is_valid, message=message)
    except Exception as exc:  # noqa: BLE001 — we want to surface any error to the UI
        logger.exception("Credential validation failed")
        return ValidateResponse(valid=False, message=str(exc))


# ---------------------------------------------------------------------------
# Static option lists (helpful for select dropdowns in the frontend)
# ---------------------------------------------------------------------------

_LANGUAGE_LABELS = {
    LanguageCode.AUTO: "Auto-detect",
    LanguageCode.VIETNAMESE: "Vietnamese",
    LanguageCode.ENGLISH: "English",
    LanguageCode.JAPANESE: "Japanese",
    LanguageCode.CHINESE_SIMPLIFIED: "Chinese (Simplified)",
    LanguageCode.CHINESE_TRADITIONAL: "Chinese (Traditional)",
}

_SERVICE_MODELS = {
    TranslationService.ARGOS: ("Argos Translate (offline)", ["argostranslate"]),
    TranslationService.OPENAI: ("OpenAI", ["gpt-4.1"]),
    TranslationService.GEMINI: ("Google Gemini", ["gemini-1.5-flash"]),
    TranslationService.ANTHROPIC: (
        "Anthropic Claude",
        ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
    ),
}


@router.get("/options", response_model=OptionsResponse)
async def get_options() -> OptionsResponse:
    return OptionsResponse(
        languages=[LanguageOption(code=c.value, label=_LANGUAGE_LABELS[c]) for c in LanguageCode],
        services=[
            ServiceOption(code=s.value, label=_SERVICE_MODELS[s][0], models=_SERVICE_MODELS[s][1])
            for s in TranslationService
        ],
    )


# ---------------------------------------------------------------------------
# Translation cache
# ---------------------------------------------------------------------------


@router.get("/cache", response_model=CacheStatsResponse)
async def get_cache_stats() -> CacheStatsResponse:
    stats = get_translation_cache().stats()
    return CacheStatsResponse(**stats) if stats else CacheStatsResponse()


@router.delete("/cache", response_model=CacheClearResponse)
async def clear_cache(scope: str = "all") -> CacheClearResponse:
    """Clear the on-disk translation cache. `scope=expired` only reaps stale
    entries; `scope=all` wipes everything (e.g. when the user changes models)."""
    cache = get_translation_cache()
    if scope == "expired":
        removed = cache.clear_expired()
    else:
        scope = "all"
        removed = cache.clear_all()
    return CacheClearResponse(removed=removed, scope=scope)
