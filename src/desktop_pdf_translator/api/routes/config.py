"""Configuration + API key management endpoints."""

import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException

from ...processors.pdf_cache import get_pdf_cache
from ...config import (
    AppSettings,
    LanguageCode,
    TranslationService,
    get_config_manager,
    get_settings,
)
from ...translators import TranslatorFactory, get_translation_cache
from ...translators.capabilities import (
    LANGUAGE_LABELS,
    SERVICE_LABELS,
    supported_pairs_for,
)
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


# Deadline for the auto-promotion probe below. Generous enough for a cold
# TLS handshake to a provider, short enough that Settings → Save still feels
# like a save.
_VALIDATE_PROBE_TIMEOUT_S = 20.0


async def _credentials_work(
    service: TranslationService, service_config: dict
) -> tuple[bool, str]:
    """Probe a just-saved key the same way `POST /config/validate` does.

    Runs off the event loop — `validate_configuration()` is a blocking HTTP
    call to the provider — and under a deadline, because this sits on the
    Settings *save* path now, not just behind the Validate button. OpenAI and
    Anthropic pass their own `timeout=10`; Gemini passes none, so without this
    a save on a flaky connection would hang for the SDK's default.

    A timeout reports the same thing as a rejection: don't promote. The key is
    still saved, and the user can switch services explicitly.
    """

    def probe() -> tuple[bool, str]:
        kwargs = {"api_key": service_config.get("api_key")}
        # Only override the model when we have one: passing `model=None`
        # replaces the backend's own default with None.
        if service_config.get("model"):
            kwargs["model"] = service_config["model"]
        translator = TranslatorFactory.create_translator(
            service=service, lang_in="en", lang_out="vi", **kwargs
        )
        return translator.validate_configuration()

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(probe), timeout=_VALIDATE_PROBE_TIMEOUT_S
        )
    except (asyncio.TimeoutError, TimeoutError):
        # The thread is left to finish on its own; nothing reads its result.
        return False, f"timed out contacting {service.value}"
    except Exception as exc:  # noqa: BLE001 — any failure means "don't promote"
        return False, str(exc)


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
        # An explicit choice is the user's to make — honoured unconditionally.
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
        # Promote only on a key that actually works. Moving the user off Argos
        # on a typo'd key used to hand them a translator that fails every
        # paragraph, silently, for every document from then on — while Argos
        # would have kept working. One provider round-trip, and only on the
        # rare "first key saved while still on Argos" path.
        ok, message = await _credentials_work(chosen, current[chosen.value])
        if ok:
            current["translation"]["preferred_service"] = chosen.value
            logger.info(
                "Auto-switching preferred_service argos -> %s after key save",
                chosen.value,
            )
        else:
            logger.warning(
                "Key saved for %s but it did not validate (%s) — staying on "
                "Argos. The user can still switch services explicitly.",
                chosen.value,
                message,
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
    if payload.cache_translated_pdfs is not None:
        current["translation"]["cache_translated_pdfs"] = payload.cache_translated_pdfs

    new_settings = AppSettings(**current)
    # Off the loop thread: `save_settings` fsyncs and rewrites the backup, and
    # this loop is also carrying any in-flight translation's SSE stream.
    if not await asyncio.to_thread(mgr.save_settings, new_settings):
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

# Labels come from the capability module so the dropdown and the "unsupported
# pair" error message can never name the same language differently.
_SERVICE_MODELS = {
    TranslationService.ARGOS: ["argostranslate"],
    TranslationService.OPENAI: ["gpt-4.1"],
    TranslationService.GEMINI: ["gemini-1.5-flash"],
    TranslationService.ANTHROPIC: [
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ],
}


@router.get("/options", response_model=OptionsResponse)
async def get_options() -> OptionsResponse:
    return OptionsResponse(
        languages=[LanguageOption(code=c.value, label=LANGUAGE_LABELS[c]) for c in LanguageCode],
        services=[
            ServiceOption(
                code=s.value,
                label=SERVICE_LABELS[s],
                models=_SERVICE_MODELS[s],
                supported_pairs=supported_pairs_for(s),
            )
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
async def clear_cache(scope: str = "all", target: str = "paragraph") -> CacheClearResponse:
    """Clear the on-disk translation caches.

    `scope=expired` only reaps stale entries; `scope=all` wipes everything
    (e.g. when the user changes models). `target` picks which cache:
    `paragraph` (default, backward compatible), `pdf` (whole-PDF cache), or
    `all` (both). The PDF cache has no TTL, so `scope=expired` doesn't touch it.
    """
    removed = 0
    if target in ("paragraph", "all"):
        cache = get_translation_cache()
        if scope == "expired":
            removed += await asyncio.to_thread(cache.clear_expired)
        else:
            scope = "all"
            removed += await asyncio.to_thread(cache.clear_all)
    if target in ("pdf", "all") and scope != "expired":
        removed += await asyncio.to_thread(get_pdf_cache().clear_all)
    return CacheClearResponse(removed=removed, scope=scope)
