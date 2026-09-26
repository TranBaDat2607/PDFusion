"""Configuration + API key management endpoints."""

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException

from ...processors.pdf_cache import get_pdf_cache
from ...config import (
    AppSettings,
    LanguageCode,
    ModelRef,
    ProviderId,
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
    PROVIDERS,
    ProviderSpec,
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
    TranslationConfig,
    ValidateRequest,
    ValidateResponse,
)
from .providers import (
    apply_key_and_endpoint,
    catalog_call,
    catalog_for,
    endpoint_change_refusal,
    save_settings,
    verify,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config", tags=["config"], dependencies=[Depends(require_token)])


def _mask(settings: AppSettings, spec: ProviderSpec) -> APIKeyMaskedSettings:
    saved = settings.providers[spec.id]
    return APIKeyMaskedSettings(
        has_key=spec.requires_key and bool(saved.api_key),
        model=settings.model_for(spec.id),
        base_url=saved.base_url,
        extra={},
    )


@router.get("", response_model=ConfigResponse)
async def get_config() -> ConfigResponse:
    s = get_settings()
    return ConfigResponse(
        **{spec.id: _mask(s, spec) for spec in PROVIDERS},
        translation=TranslationConfig(
            **s.translation.model_dump(), preferred_service=s.translation.model.provider
        ),
        rag=s.rag,
        gui=s.gui,
        processing=s.processing,
        debug_mode=s.debug_mode,
    )


def _choose_model(settings: AppSettings, service: TranslationService, model: str) -> None:
    """A per-provider block's `model`: what that provider runs from now on,
    and so the translation model when it is the provider translating."""
    if settings.translation.model.provider == service:
        settings.translate_with(ModelRef(provider=service, model=model))
    else:
        settings.remember_model(service, model)


@router.put("", response_model=ConfigResponse)
async def update_config(payload: ConfigUpdateRequest) -> ConfigResponse:
    mgr = get_config_manager()
    current = mgr.settings.model_dump()

    # The per-provider blocks, for the providers that take a key.
    blocks = [
        (TranslationService(p), getattr(payload, p))
        for p in keyed_ids()
        if getattr(payload, p) is not None
    ]
    # Track which LLM services received a non-empty key in *this* PUT, so we
    # can auto-promote the translation off Argos to that LLM (in
    # `ProviderSpec.priority` order if several keys arrive at once).
    newly_keyed: list[TranslationService] = []
    rekeyed: list[str] = []
    # Providers whose catalog rows describe a key or endpoint this PUT
    # replaces. Nothing in a row is derived from the key, so a change can't be
    # told from the row itself; it has to be dropped here.
    stale_catalog: list[str] = []

    # Endpoint changes are all vetted before anything is applied, so a
    # refusal leaves `current` and the manager's preserved-key records
    # untouched, instead of part-way through.
    for service, update in blocks:
        refusal = endpoint_change_refusal(
            current, provider(service.value), update.api_key, getattr(update, "base_url", None)
        )
        if refusal:
            raise HTTPException(status_code=422, detail=refusal)

    for service, update in blocks:
        change = apply_key_and_endpoint(
            current, provider(service.value), update.api_key, getattr(update, "base_url", None)
        )
        if change.rekeyed:
            rekeyed.append(service.value)
        if change.stale_catalog:
            stale_catalog.append(service.value)
        if change.new_key:
            newly_keyed.append(service)

    # Model choices go through the settings object, which knows how a choice
    # for one provider relates to the translation model.
    draft = AppSettings(**current)
    for service, update in blocks:
        if update.model is not None:
            _choose_model(draft, service, update.model)

    promotion_listing = None
    promotion_refused = False
    chosen = None
    if payload.preferred_service is not None:
        # An explicit choice is the user's to make — honoured unconditionally.
        service = payload.preferred_service
        draft.translate_with(ModelRef(provider=service, model=draft.model_for(service)))
    elif draft.translation.model.provider == TranslationService.ARGOS and newly_keyed:
        priority = tuple(TranslationService(p) for p in llm_ids_by_priority())
        chosen = next((s for s in priority if s in newly_keyed), newly_keyed[0])
        # Promote only on a key that actually works, with a model it can use.
        # Moving the user off Argos on a typo'd key used to hand them a
        # translator that fails every paragraph, silently, for every document
        # from then on — while Argos would have kept working. One listing,
        # never a completion (#84), and only on the rare "first key saved while
        # still on Argos" path.
        section = draft.providers[chosen.value]
        model = draft.model_for(chosen)
        ok = False
        try:
            promotion_listing = await catalog.list_models(
                chosen.value, section.api_key, section.base_url
            )
        except (catalog.KeyRejected, catalog.ListingFailed) as exc:
            promotion_refused = isinstance(exc, catalog.KeyRejected)
            message = str(exc)
        else:
            listed = promotion_listing.models + promotion_listing.hidden
            ok = model_matches(model, (m.id for m in listed))
            message = f"{model} is not among the models it can use"
        if ok:
            draft.translate_with(ModelRef(provider=chosen, model=model))
            logger.info("Auto-switching translation argos -> %s after key save", chosen.value)
        else:
            logger.warning(
                "Key saved for %s but it did not validate (%s) — staying on "
                "Argos. The user can still switch services explicitly.",
                chosen.value,
                message,
            )

    # Last, so it wins over the per-provider blocks and `preferred_service`.
    if payload.translation_model is not None:
        draft.translate_with(payload.translation_model)
    if "answer_model" in payload.model_fields_set:
        draft.rag.answer_model = payload.answer_model

    current = draft.model_dump()
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

    saved = await save_settings(current, rekeyed=rekeyed, stale_catalog=stale_catalog)

    if promotion_listing is not None:
        # The promotion listed exactly the pair just saved — and after the
        # invalidation above, which would otherwise drop it again. Keeping it
        # spares the picker a second round-trip.
        await catalog_call(
            "put", chosen.value, saved.providers[chosen.value].base_url, promotion_listing
        )
    elif promotion_refused:
        await catalog_call("mark_invalid", chosen.value, saved.providers[chosen.value].base_url)
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

    model = payload.model or get_settings().model_for(payload.service)
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
async def list_endpoint_models(service: ProviderId) -> EndpointModelsResponse:
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
