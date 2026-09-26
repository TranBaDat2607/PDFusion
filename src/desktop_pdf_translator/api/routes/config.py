"""Configuration endpoints: languages, the models chosen, limits and caches.

Keys, endpoints and each provider's models are `routes/providers.py`'s:
`PUT /config` used to carry a block per provider, and `POST /config/validate`
and `GET /config/models/{service}` to check and list them, until #88 retired
that per-service API for the one `/providers` has.
"""

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends

from ...processors.pdf_cache import get_pdf_cache
from ...config import LanguageCode, TranslationService, get_config_manager, get_settings
from ...translators.translation_cache import get_translation_cache
from ...translators.capabilities import (
    LANGUAGE_LABELS,
    SERVICE_LABELS,
    supported_pairs_for,
)
from ...providers.registry import provider
from ..auth import require_token
from ..schemas import (
    CacheClearResponse,
    CacheOverviewResponse,
    CacheStatsResponse,
    ConfigResponse,
    ConfigUpdateRequest,
    OptionsResponse,
    LanguageOption,
    ServiceOption,
    PdfCacheStatsResponse,
    TranslationConfig,
)
from .providers import save_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config", tags=["config"], dependencies=[Depends(require_token)])


@router.get("", response_model=ConfigResponse)
async def get_config() -> ConfigResponse:
    s = get_settings()
    return ConfigResponse(
        translation=TranslationConfig(**s.translation.model_dump()),
        rag=s.rag,
        gui=s.gui,
        processing=s.processing,
        debug_mode=s.debug_mode,
    )


@router.put("", response_model=ConfigResponse)
async def update_config(payload: ConfigUpdateRequest) -> ConfigResponse:
    """Save the models chosen, the languages, the limits and the switches.

    No key or endpoint changes here, so nothing here can send a key anywhere
    new; `PUT /providers/{id}` is where those change, under the rule that a
    saved key only goes to the endpoint it was saved for.
    """
    draft = get_config_manager().settings.model_copy(deep=True)
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

    await save_settings(current)
    return await get_config()


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
