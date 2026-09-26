"""Providers: which exist, the state of each key, and the models it can use (#84).

A key is verified, and its models discovered, by *listing* them — never by
generating text. Every provider serves its list for free to any valid key, so
a 401/403 there says the key is wrong without a completion being paid for.
What a listing cannot see (a key with no credit, a model the account may not
run) is in architecture-notes § "LLM endpoints and models".

`/config/validate` and `/config/models/{service}` are thin wrappers over
`verify` and `catalog_for` here, until the per-service API is retired (#88).
"""

import asyncio
import logging
import sqlite3
from typing import Iterable, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException

from ...config import TranslationService, get_config_manager, get_settings
from ...providers import catalog
from ...providers.listing import ListedModel, Listing, model_matches
from ...providers.registry import PROVIDERS, ProviderSpec, provider
from ...storage.sqlite import ms_to_iso, now_ms
from ..auth import require_token
from ..schemas import (
    KeyState,
    ModelCatalogResponse,
    ModelRecord,
    ProviderInfo,
    ProvidersResponse,
    VerifyRequest,
    VerifyResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/providers", tags=["providers"], dependencies=[Depends(require_token)])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def catalog_call(method: str, *args, **kwargs):
    """Call a catalog method off the loop. The catalog is a disposable cache,
    so a failure there is logged and read as a miss rather than failing a
    request that has everything it needs without it."""
    store = catalog.get_model_catalog()
    try:
        return await asyncio.to_thread(getattr(store, method), *args, **kwargs)
    except (sqlite3.Error, OSError) as exc:
        logger.warning("Model catalog %s failed: %s", method, exc)
        return None


def _iso(ms: Optional[int]) -> Optional[str]:
    return ms_to_iso(ms) if ms is not None else None


def _record(model: ListedModel, source: str) -> ModelRecord:
    return ModelRecord(
        id=model.id,
        display_name=model.display_name,
        context_tokens=model.context_tokens,
        output_tokens=model.output_tokens,
        created_at=_iso(model.created_at),
        source=source,
    )


def _saved_first(saved_model: Optional[str], offered: Iterable[str]) -> List[ModelRecord]:
    """The saved model as a record of its own, when nothing offered has it:
    a name typed by hand must never disappear from the picker."""
    if saved_model and saved_model not in set(offered):
        return [ModelRecord(id=saved_model, source="saved")]
    return []


def _fallback_records(
    spec: ProviderSpec, saved_model: Optional[str], custom_endpoint: bool
) -> List[ModelRecord]:
    """What to offer with no list: the saved model, and on the provider's own
    endpoint its suggestions — which Ollama or LM Studio don't have."""
    suggested = [] if custom_endpoint else list(spec.suggested_models)
    return _saved_first(saved_model, suggested) + [
        ModelRecord(id=model, source="suggested") for model in suggested
    ]


def _listed_response(
    listing: Listing,
    fetched_at: Optional[int],
    saved_model: Optional[str],
    key_state: KeyState,
    error: Optional[str] = None,
) -> ModelCatalogResponse:
    return ModelCatalogResponse(
        models=_saved_first(saved_model, (m.id for m in listing.models))
        + [_record(m, "listed") for m in listing.models],
        hidden=[_record(m, "listed") for m in listing.hidden],
        fetched_at=_iso(fetched_at),
        key_state=key_state,
        error=error,
    )


def _saved(service_id: str):
    return getattr(get_settings(), service_id)


def resolve_probe_target(
    spec: ProviderSpec,
    saved,
    typed_key: Optional[str],
    typed_base_url: Optional[str],
) -> Tuple[Optional[str], Optional[str], bool]:
    """Which key goes to which endpoint: `(api_key, base_url, is_saved_pair)`.

    A saved key is only ever sent to the endpoint it was saved for — the rule
    `PUT /config` keeps (#32). `base_url` `None` is the saved endpoint and
    `""` the provider's own; naming another one needs the key typed alongside
    it, or this is a 422. `api_key` is `None` when there is nothing to send.
    """
    saved_base_url = getattr(saved, "base_url", None)
    if not spec.takes_endpoint:
        base_url = None
    elif typed_base_url is None:
        base_url = saved_base_url
    else:
        base_url = typed_base_url or None
    same_endpoint = base_url == saved_base_url

    if typed_key:
        return typed_key, base_url, same_endpoint and typed_key == saved.api_key
    if not same_endpoint:
        raise HTTPException(
            status_code=422,
            detail="Enter the API key to check a different endpoint.",
        )
    return saved.api_key or None, base_url, True


def _keyed_spec(service: TranslationService) -> ProviderSpec:
    spec = provider(service.value)
    if not spec.requires_key:
        raise HTTPException(
            status_code=422, detail=f"{spec.label} has no API key to check."
        )
    return spec


# ---------------------------------------------------------------------------
# the two operations, shared with /config
# ---------------------------------------------------------------------------


async def verify(
    service: TranslationService,
    typed_key: Optional[str],
    typed_base_url: Optional[str],
    model: Optional[str],
) -> VerifyResponse:
    """List with a key, saving nothing to the config.

    The catalog is written only when what was listed is the saved key at the
    saved endpoint: a typed key the user may yet discard must not mark the
    saved one valid, or invalid.
    """
    spec = _keyed_spec(service)
    api_key, base_url, saved_pair = resolve_probe_target(
        spec, _saved(spec.id), typed_key, typed_base_url
    )
    if api_key is None:
        if get_config_manager().has_unreadable_key(spec.id):
            return VerifyResponse(
                valid=False,
                key_state="unreadable",
                message="The saved API key couldn't be read. Enter it again.",
            )
        return VerifyResponse(
            valid=False, key_state="unverified", message="Enter an API key first"
        )

    try:
        listing = await catalog.list_models(spec.id, api_key, base_url)
    except catalog.KeyRejected as exc:
        if saved_pair:
            await catalog_call("mark_invalid", spec.id, base_url)
        return VerifyResponse(
            valid=False,
            key_state="invalid",
            message=f"{spec.label} rejected this API key: {exc}",
        )
    except catalog.ListingFailed as exc:
        return VerifyResponse(valid=False, key_state="unverified", message=str(exc))

    if saved_pair:
        await catalog_call("put", spec.id, base_url, listing)
    model_found = (
        model_matches(model, (m.id for m in listing.models + listing.hidden))
        if model
        else None
    )
    if model_found is False:
        message = f"The key works, but {model} isn't among the models it can use."
    else:
        message = f"The key works: {len(listing.models)} models available."
    return VerifyResponse(
        valid=True,
        key_state="valid",
        message=message,
        model_found=model_found,
        models=[_record(m, "listed") for m in listing.models],
        hidden=[_record(m, "listed") for m in listing.hidden],
    )


async def catalog_for(service: TranslationService, refresh: bool) -> ModelCatalogResponse:
    """The models the saved key can use at the saved endpoint.

    Served from the catalog while fresh; listed otherwise, or on `refresh`.
    Every failure is `error` in a 200, with the saved model still on offer: a
    local server that isn't up yet is ordinary.
    """
    spec = provider(service.value)
    if not spec.requires_key:
        return ModelCatalogResponse(
            models=[ModelRecord(id=m, source="suggested") for m in spec.suggested_models]
        )

    saved = _saved(spec.id)
    base_url = getattr(saved, "base_url", None)
    fallback = _fallback_records(spec, saved.model, custom_endpoint=base_url is not None)
    if not saved.api_key:
        unreadable = get_config_manager().has_unreadable_key(spec.id)
        return ModelCatalogResponse(
            models=fallback,
            key_state="unreadable" if unreadable else "unverified",
            error="No API key is saved.",
        )

    entry = await catalog_call("get", spec.id, base_url)
    if entry is not None and not refresh and catalog.is_fresh(entry):
        return _listed_response(entry.listing, entry.fetched_at, saved.model, entry.key_state)

    try:
        listing = await catalog.list_models(spec.id, saved.api_key, base_url)
    except catalog.KeyRejected as exc:
        await catalog_call("mark_invalid", spec.id, base_url)
        return ModelCatalogResponse(
            models=fallback,
            key_state="invalid",
            error=f"{spec.label} rejected the saved API key: {exc}",
        )
    except catalog.ListingFailed as exc:
        logger.info("Listing %s models failed: %s", spec.id, exc)
        if entry is not None and entry.listing is not None:
            # A stale list beats none while the server is away.
            return _listed_response(
                entry.listing, entry.fetched_at, saved.model, entry.key_state, str(exc)
            )
        return ModelCatalogResponse(
            models=fallback,
            key_state=entry.key_state if entry is not None else "unverified",
            error=str(exc),
        )

    now = now_ms()
    await catalog_call("put", spec.id, base_url, listing, now=now)
    return _listed_response(listing, now, saved.model, "valid")


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


@router.get("", response_model=ProvidersResponse)
async def list_providers() -> ProvidersResponse:
    """Every provider, with where its key stands. Reads only; never lists."""
    settings = get_settings()
    mgr = get_config_manager()
    providers = []
    for spec in PROVIDERS:
        saved = getattr(settings, spec.id)
        has_key = bool(getattr(saved, "api_key", None))
        base_url = getattr(saved, "base_url", None)
        entry = await catalog_call("get", spec.id, base_url) if has_key else None
        if spec.requires_key and mgr.has_unreadable_key(spec.id):
            key_state: KeyState = "unreadable"
        elif entry is not None:
            key_state = entry.key_state
        else:
            key_state = "unverified"
        providers.append(
            ProviderInfo(
                id=TranslationService(spec.id),
                label=spec.label,
                short_label=spec.short_label,
                protocol=spec.protocol,
                requires_key=spec.requires_key,
                takes_endpoint=spec.takes_endpoint,
                default_base_url=spec.default_base_url,
                default_model=spec.default_model,
                suggested_models=list(spec.suggested_models),
                model_is_fixed=spec.model_is_fixed,
                signup_url=spec.signup_url,
                has_key=has_key,
                base_url=base_url,
                key_state=key_state,
                last_verified_at=_iso(entry.verified_at) if entry else None,
                catalog_fetched_at=_iso(entry.fetched_at) if entry else None,
                catalog_fresh=bool(entry and catalog.is_fresh(entry)),
            )
        )
    return ProvidersResponse(providers=providers)


@router.get("/{provider_id}/models", response_model=ModelCatalogResponse)
async def provider_models(
    provider_id: TranslationService, refresh: bool = False
) -> ModelCatalogResponse:
    return await catalog_for(provider_id, refresh)


@router.post("/{provider_id}/verify", response_model=VerifyResponse)
async def verify_provider(
    provider_id: TranslationService, payload: VerifyRequest
) -> VerifyResponse:
    return await verify(provider_id, payload.api_key, payload.base_url, payload.model)
