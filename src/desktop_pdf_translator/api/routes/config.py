"""Configuration + API key management endpoints."""

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException

from ...processors.pdf_cache import get_pdf_cache
from ...config import (
    AppSettings,
    LanguageCode,
    TranslationService,
    get_config_manager,
    get_settings,
)
from ...translators.translation_cache import get_translation_cache
from ...translators.capabilities import (
    LANGUAGE_LABELS,
    SERVICE_LABELS,
    supported_pairs_for,
)
from ..auth import require_token
from ..schemas import (
    APIKeyMaskedSettings,
    CacheClearResponse,
    CacheOverviewResponse,
    CacheStatsResponse,
    ConfigResponse,
    ConfigUpdateRequest,
    OptionsResponse,
    LanguageOption,
    PdfCacheStatsResponse,
    ServiceOption,
    ValidateRequest,
    ValidateResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config", tags=["config"], dependencies=[Depends(require_token)])


# Deadline for a credentials probe. Generous enough for a cold TLS handshake to
# a provider, short enough that Settings → Save still feels like a save.
_VALIDATE_PROBE_TIMEOUT_S = 20.0

# The services whose settings carry a `base_url`: each can be pointed at another
# server that speaks its API — Ollama, LM Studio, a proxy (#32).
_ENDPOINT_SERVICES = (TranslationService.OPENAI, TranslationService.ANTHROPIC)


def _probe_kwargs(service_config: dict) -> dict:
    """What a credentials probe hands `TranslatorFactory`.

    `api_key` and `base_url` go whenever the config names them, `None`
    included: the factory starts from the *saved* settings, so leaving
    `base_url` out would probe the saved endpoint instead of the one being
    checked. The model only when there is one, because `model=None` would
    replace the backend's own default with None.
    """
    kwargs = {
        name: service_config[name]
        for name in ("api_key", "base_url")
        if name in service_config
    }
    if service_config.get("model"):
        kwargs["model"] = service_config["model"]
    return kwargs


async def _credentials_work(
    service: TranslationService, service_config: dict
) -> tuple[bool, str]:
    """Probe credentials with the provider: `POST /config/validate`, and the
    auto-promotion in `PUT /config`.

    Runs off the event loop — `validate_configuration()` is a blocking HTTP
    call to the provider — and under a deadline. OpenAI and Anthropic pass
    their own `timeout=10`; Gemini passes none, so without this a probe on a
    flaky connection would hang for the SDK's default, and on the loop it used
    to freeze the whole sidecar meanwhile.

    A timeout reports the same thing as a rejection.
    """

    def probe() -> tuple[bool, str]:
        from ...translators.factory import TranslatorFactory

        translator = TranslatorFactory.create_translator(
            service=service, lang_in="en", lang_out="vi", **_probe_kwargs(service_config)
        )
        return translator.validate_configuration()

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(probe), timeout=_VALIDATE_PROBE_TIMEOUT_S
        )
    except (asyncio.TimeoutError, TimeoutError):
        # The thread is left to finish on its own; nothing reads its result.
        return False, f"timed out contacting {service.value}"
    except Exception as exc:  # noqa: BLE001 — any failure is "not valid"
        return False, str(exc)


def _mask(service_settings) -> APIKeyMaskedSettings:
    # ArgosSettings has no api_key attribute, so getattr falls through to False.
    return APIKeyMaskedSettings(
        has_key=bool(getattr(service_settings, "api_key", None)),
        model=service_settings.model,
        base_url=getattr(service_settings, "base_url", None),
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
        # Pass the settings objects straight through — ConfigResponse's fields
        # are now real nested models, not Dict[str, Any], so there's nothing
        # left for model_dump() to do here. Same wire format either way.
        translation=s.translation,
        rag=s.rag,
        gui=s.gui,
        processing=s.processing,
        debug_mode=s.debug_mode,
    )


@router.put("", response_model=ConfigResponse)
async def update_config(payload: ConfigUpdateRequest) -> ConfigResponse:
    mgr = get_config_manager()
    current = mgr.settings.model_dump()

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
        section = current[service.value]
        if update.api_key is not None:
            new_key = update.api_key or None
            section["api_key"] = new_key
            if new_key:
                newly_keyed.append(service)
        if update.model is not None:
            section["model"] = update.model
        base_url = getattr(update, "base_url", None)
        if base_url is not None and (base_url or None) != section.get("base_url"):
            # A saved key is only ever sent to the endpoint it was saved for.
            # `GET /config` never hands a key out; without this, anything able
            # to call `PUT /config` could point the endpoint at a server of its
            # own and read the key off the next request (#32).
            if section.get("api_key") and update.api_key is None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Enter the {SERVICE_LABELS[service]} API key again "
                        "to change its endpoint."
                    ),
                )
            section["base_url"] = base_url or None

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
    if payload.chat_enabled is not None:
        current["rag"]["chat_enabled"] = payload.chat_enabled
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
    """Check credentials with the provider.

    Whatever the request leaves out comes from the saved settings: the key,
    the model, the endpoint. The saved key is only sent to the saved endpoint,
    the rule `PUT /config` keeps; checking another endpoint needs the key typed
    alongside it.
    """
    if payload.service == TranslationService.ARGOS:
        # Argos has no API key — report the install state. Off the loop, but
        # without the probe's deadline: its first check imports argostranslate,
        # which is slow on a cold machine without being a call that can hang.
        def check_argos() -> tuple[bool, str]:
            from ...translators.factory import TranslatorFactory

            translator = TranslatorFactory.create_translator(
                service=TranslationService.ARGOS,
                lang_in="en",
                lang_out="vi",
            )
            return translator.validate_configuration()

        try:
            is_valid, message = await asyncio.to_thread(check_argos)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Argos validation failed")
            return ValidateResponse(valid=False, message=str(exc))
        return ValidateResponse(valid=is_valid, message=message)

    saved = getattr(get_settings(), payload.service.value)
    service_config: dict = {"model": payload.model or saved.model}
    endpoint_changed = False
    if payload.service in _ENDPOINT_SERVICES:
        base_url = (
            saved.base_url if payload.base_url is None else payload.base_url or None
        )
        service_config["base_url"] = base_url
        endpoint_changed = base_url != saved.base_url

    if payload.api_key:
        service_config["api_key"] = payload.api_key
    elif endpoint_changed:
        raise HTTPException(
            status_code=422,
            detail="Enter the API key to check a different endpoint.",
        )
    elif saved.api_key:
        service_config["api_key"] = saved.api_key
    else:
        return ValidateResponse(valid=False, message="Enter an API key first")

    is_valid, message = await _credentials_work(payload.service, service_config)
    return ValidateResponse(valid=is_valid, message=message)


# ---------------------------------------------------------------------------
# Static option lists (helpful for select dropdowns in the frontend)
# ---------------------------------------------------------------------------

# Suggestions for the model field, each service's default first. Not a
# whitelist: the field takes any name, which is what a local server's models
# need (#32). `tests/test_config_api.py` fails when a default is missing from
# its list, which is how `gemini-1.5-flash` stayed on offer after Google
# retired it.
_SERVICE_MODELS = {
    TranslationService.ARGOS: ["argostranslate"],
    TranslationService.OPENAI: [
        "gpt-4.1",
        "gpt-5.6-luna",
        "gpt-5.6-terra",
        "gpt-5.6-sol",
        "gpt-6-astra",
    ],
    TranslationService.GEMINI: [
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
    ],
    TranslationService.ANTHROPIC: [
        "claude-sonnet-4-6",
        "claude-sonnet-5",
        "claude-opus-5",
        "claude-haiku-4-5-20251001",
    ],
}


@router.get("/options", response_model=OptionsResponse)
async def get_options() -> OptionsResponse:
    # Labels come from the capability module so the dropdown and the
    # "unsupported pair" error message can never name the same language
    # differently.
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


@router.get("/cache", response_model=CacheOverviewResponse)
async def get_cache_stats() -> CacheOverviewResponse:
    # SQLite, and on the first use after an upgrade a schema migration: off the
    # event loop, singletons included, like the clear paths below.
    paragraph, pdf = await asyncio.gather(
        asyncio.to_thread(lambda: get_translation_cache().stats()),
        asyncio.to_thread(lambda: get_pdf_cache().stats()),
    )
    return CacheOverviewResponse(
        paragraph=CacheStatsResponse(**paragraph) if paragraph else CacheStatsResponse(),
        pdf=PdfCacheStatsResponse(**pdf) if pdf else PdfCacheStatsResponse(),
    )


@router.delete("/cache", response_model=CacheClearResponse)
async def clear_cache(
    scope: Literal["all", "expired"] = "all",
    target: Literal["paragraph", "pdf", "all"] = "paragraph",
) -> CacheClearResponse:
    """Clear the on-disk translation caches.

    `target` names which: `paragraph` (the default), `pdf` (the whole-PDF
    cache) or `all`. `scope=expired` reaps only entries past their TTL, which
    only the paragraph cache has, so it never touches the PDF cache. Anything
    else is a 422: a mistyped target used to fall through to clearing.
    """
    removed = 0
    if target in ("paragraph", "all"):
        cache = get_translation_cache()
        clear = cache.clear_expired if scope == "expired" else cache.clear_all
        removed += await asyncio.to_thread(clear)
    if target in ("pdf", "all") and scope == "all":
        removed += await asyncio.to_thread(lambda: get_pdf_cache().clear_all())
    return CacheClearResponse(removed=removed, scope=scope, target=target)
