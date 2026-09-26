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
from ...providers import catalog
from ...providers.listing import model_matches
from ...providers.registry import (
    keyed_ids,
    llm_ids_by_priority,
    provider,
)
from ..auth import require_token
from ..schemas import (
    APIKeyMaskedSettings,
    CacheClearResponse,
    CacheOverviewResponse,
    CacheStatsResponse,
    ConfigResponse,
    ConfigUpdateRequest,
    EndpointModelsResponse,
    OptionsResponse,
    LanguageOption,
    PdfCacheStatsResponse,
    ServiceOption,
    ValidateRequest,
    ValidateResponse,
)
from .providers import catalog_call, catalog_for, verify

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config", tags=["config"], dependencies=[Depends(require_token)])


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
    # (in `ProviderSpec.priority` order if several keys arrive at once).
    LLM_SERVICES = tuple(TranslationService(p) for p in keyed_ids())
    newly_keyed: list[TranslationService] = []
    # Providers whose catalog rows describe a key or endpoint this PUT
    # replaces. Nothing in a row is derived from the key, so a change can't be
    # told from the row itself; it has to be dropped here.
    rekeyed: list[TranslationService] = []

    # Endpoint changes are all vetted before anything is applied. The check
    # reads only the saved section and this service's own update, so hoisting
    # it changes no answer — but it means a refusal leaves `current` and the
    # manager's preserved-key records untouched, instead of part-way through.
    for service in LLM_SERVICES:
        update = getattr(payload, service.value)
        if update is None or update.api_key is not None:
            continue
        base_url = getattr(update, "base_url", None)
        if base_url is None:
            continue
        section = current[service.value]
        if (base_url or None) == section.get("base_url"):
            continue
        # A saved key is only ever sent to the endpoint it was saved for.
        # `GET /config` never hands a key out; without this, anything able to
        # call `PUT /config` could point the endpoint at a server of its own
        # and read the key off the next request (#32). A key the manager is
        # preserving unread counts as saved: it decrypts again once the
        # keystore is reachable, and would then go to whatever endpoint was
        # set meanwhile.
        if section.get("api_key") or mgr.has_unreadable_key(service.value):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Enter the {SERVICE_LABELS[service]} API key again "
                    "to change its endpoint."
                ),
            )

    for service in LLM_SERVICES:
        update = getattr(payload, service.value)
        if update is None:
            continue
        section = current[service.value]
        if update.api_key is not None:
            new_key = update.api_key or None
            section["api_key"] = new_key
            # Set or cleared, this is now the only key for the service: an
            # earlier ciphertext the manager is preserving because it could not
            # read it must not come back on the save below.
            mgr.forget_unreadable_key(service.value)
            rekeyed.append(service)
            if new_key:
                newly_keyed.append(service)
        if update.model is not None:
            section["model"] = update.model
        base_url = getattr(update, "base_url", None)
        if base_url is not None and (base_url or None) != section.get("base_url"):
            section["base_url"] = base_url or None
            if service not in rekeyed:
                rekeyed.append(service)

    promotion_listing = None
    promotion_refused = False
    if payload.preferred_service is not None:
        # An explicit choice is the user's to make — honoured unconditionally.
        current["translation"]["preferred_service"] = payload.preferred_service.value
    elif (
        current["translation"].get("preferred_service") == TranslationService.ARGOS.value
        and newly_keyed
    ):
        priority = tuple(TranslationService(p) for p in llm_ids_by_priority())
        chosen = next((s for s in priority if s in newly_keyed), newly_keyed[0])
        # Promote only on a key that actually works, with a model it can use.
        # Moving the user off Argos on a typo'd key used to hand them a
        # translator that fails every paragraph, silently, for every document
        # from then on — while Argos would have kept working. One listing,
        # never a completion (#84), and only on the rare "first key saved while
        # still on Argos" path.
        section = current[chosen.value]
        ok = False
        try:
            promotion_listing = await catalog.list_models(
                chosen.value, section["api_key"], section.get("base_url")
            )
        except (catalog.KeyRejected, catalog.ListingFailed) as exc:
            promotion_refused = isinstance(exc, catalog.KeyRejected)
            message = str(exc)
        else:
            listed = promotion_listing.models + promotion_listing.hidden
            ok = model_matches(section["model"], (m.id for m in listed))
            message = f"{section['model']} is not among the models it can use"
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
    if payload.max_pages is not None:
        current["translation"]["max_pages"] = payload.max_pages
    if payload.max_file_size_mb is not None:
        current["translation"]["max_file_size_mb"] = payload.max_file_size_mb
    if payload.cache_translations is not None:
        current["translation"]["cache_translations"] = payload.cache_translations
    if payload.cache_translated_pdfs is not None:
        current["translation"]["cache_translated_pdfs"] = payload.cache_translated_pdfs

    new_settings = AppSettings(**current)
    # Off the loop thread: `save_settings` fsyncs and rewrites the backup, and
    # this loop is also carrying any in-flight translation's SSE stream.
    if not await asyncio.to_thread(mgr.save_settings, new_settings):
        # Nothing was written, so a key dropped from the preserved-ciphertext
        # records above is still in the file. Re-read it, or a later unrelated
        # save would blank the value this request failed to replace.
        await asyncio.to_thread(mgr.load_settings)
        raise HTTPException(status_code=500, detail="Failed to save settings")
    mgr._settings = new_settings  # refresh cached singleton

    # After the save, never before: a refused save must leave the catalog
    # describing the key that is still in the file.
    for service in rekeyed:
        await catalog_call("invalidate", service.value)
    if promotion_listing is not None:
        # The promotion listed exactly the pair just saved — and after the
        # invalidation above, which would otherwise drop it again. Keeping it
        # spares the picker a second round-trip.
        await catalog_call(
            "put", chosen.value, current[chosen.value].get("base_url"), promotion_listing
        )
    elif promotion_refused:
        await catalog_call("mark_invalid", chosen.value, current[chosen.value].get("base_url"))
    return await get_config()


@router.post("/validate", response_model=ValidateResponse)
async def validate_credentials(payload: ValidateRequest) -> ValidateResponse:
    """Check credentials with the provider, by listing the key's models.

    A wrapper over `POST /providers/{id}/verify`. Whatever the request leaves
    out comes from the saved settings: the key, the model, the endpoint. The
    saved key is only sent to the saved endpoint, the rule `PUT /config`
    keeps; checking another endpoint needs the key typed alongside it.
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

    model = payload.model or getattr(get_settings(), payload.service.value).model
    result = await verify(payload.service, payload.api_key, payload.base_url, model)
    # Save's check: the key works *and* the model is one it can use, so a
    # mistyped name turns up before a document fails on it paragraph by
    # paragraph. Listing, never a completion (#84).
    return ValidateResponse(
        valid=result.valid and result.model_found is not False, message=result.message
    )


# ---------------------------------------------------------------------------
# Static option lists (helpful for select dropdowns in the frontend)
# ---------------------------------------------------------------------------


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
                models=list(provider(s.value).suggested_models),
                supported_pairs=supported_pairs_for(s),
            )
            for s in TranslationService
        ],
    )


@router.get("/models/{service}", response_model=EndpointModelsResponse)
async def list_endpoint_models(service: TranslationService) -> EndpointModelsResponse:
    """The ids the saved key can use at the saved endpoint.

    A wrapper over the catalog (`GET /providers/{id}/models`) for the
    toolbar's model picker: listed models only — the picker adds the saved
    model and, without a list, the suggestions itself. Always the saved key
    with the saved endpoint, the pair `PUT /config` keeps together, so this
    can't send a key anywhere new.
    """
    if not provider(service.value).requires_key:
        raise HTTPException(
            status_code=422,
            detail=f"{SERVICE_LABELS[service]} has no models to list.",
        )
    result = await catalog_for(service, refresh=False)
    listed = {model.id for model in result.models if model.source == "listed"}
    return EndpointModelsResponse(models=sorted(listed), error=result.error)


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
