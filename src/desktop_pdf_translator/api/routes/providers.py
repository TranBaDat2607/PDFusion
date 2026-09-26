"""Providers: which exist, the state of each key, and the models it can use (#84).

A key is verified, and its models discovered, by *listing* them — never by
generating text. Every provider serves its list for free to any valid key, so
a 401/403 there says the key is wrong without a completion being paid for.
What a listing cannot see (a key with no credit, a model the account may not
run) is in architecture-notes § "LLM endpoints and models".

This is the only place a key or an endpoint changes. `PUT /config` carried a
block per provider, and `/config/validate` and `/config/models/{service}`
wrapped `verify` and `catalog_for` here, until #88 retired that per-service
API.
"""

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from ...config import (
    AppSettings,
    ProviderId,
    TranslationService,
    get_config_manager,
    get_settings,
)
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
    ProviderUpdateRequest,
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
    return get_settings().providers[service_id]


def resolve_probe_target(
    spec: ProviderSpec,
    saved,
    typed_key: Optional[str],
    typed_base_url: Optional[str],
) -> Tuple[Optional[str], Optional[str], bool]:
    """Which key goes to which endpoint: `(api_key, base_url, is_saved_pair)`.

    A saved key is only ever sent to the endpoint it was saved for — the rule
    `PUT /providers/{id}` keeps (#32). `base_url` `None` is the saved endpoint
    and `""` the provider's own; naming another one needs the key typed
    alongside it, or this is a 422. `api_key` is `None` when there is nothing
    to send. A keyless provider has no key to guard, so any endpoint may be
    checked, and a key typed for it is a 422 (#88).
    """
    saved_base_url = getattr(saved, "base_url", None)
    if not spec.takes_endpoint:
        base_url = None
    elif typed_base_url is None:
        base_url = saved_base_url
    else:
        base_url = typed_base_url or None
    same_endpoint = base_url == saved_base_url

    if not spec.requires_key:
        if typed_key:
            raise HTTPException(status_code=422, detail=f"{spec.label} takes no API key.")
        return None, base_url, same_endpoint
    if typed_key:
        return typed_key, base_url, same_endpoint and typed_key == saved.api_key
    if not same_endpoint:
        raise HTTPException(
            status_code=422,
            detail="Enter the API key to check a different endpoint.",
        )
    return saved.api_key or None, base_url, True


def check_offline_engine(spec: ProviderSpec) -> Tuple[bool, str]:
    """Whether an engine with nothing to list (Argos) is installed. Blocking.

    Its translator's own check reads the installed language pack and sends
    nothing anywhere. The one seam the route tests replace.
    """
    from ...translators.factory import TranslatorFactory

    translator = TranslatorFactory.create_translator(
        service=TranslationService(spec.id), lang_in="en", lang_out="vi"
    )
    return translator.validate_configuration()


async def _verify_offline(spec: ProviderSpec, typed_key: Optional[str]) -> VerifyResponse:
    if typed_key:
        raise HTTPException(status_code=422, detail=f"{spec.label} takes no API key.")
    # Off the loop, but without the listing's deadline: the first check
    # imports argostranslate, which is slow on a cold machine without being a
    # call that can hang.
    try:
        installed, message = await asyncio.to_thread(check_offline_engine, spec)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Checking %s failed", spec.id)
        return VerifyResponse(valid=False, key_state="unverified", message=str(exc))
    return VerifyResponse(valid=installed, key_state="unverified", message=message)


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
    saved one valid, or invalid. An engine with nothing to list (Argos) is
    checked for its install instead.
    """
    spec = provider(service.value)
    if spec.lister is None:
        return await _verify_offline(spec, typed_key)
    api_key, base_url, saved_pair = resolve_probe_target(
        spec, _saved(spec.id), typed_key, typed_base_url
    )
    if api_key is None and spec.requires_key:
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
    if spec.lister is None:
        return ModelCatalogResponse(
            models=[ModelRecord(id=m, source="suggested") for m in spec.suggested_models]
        )

    saved = _saved(spec.id)
    saved_model = get_settings().model_for(spec.id)
    base_url = saved.base_url
    fallback = _fallback_records(spec, saved_model, custom_endpoint=base_url is not None)
    # A keyless server lists with no key (`catalog.list_models` hands its SDK
    # the placeholder); a keyed provider needs its own.
    if spec.requires_key and not saved.api_key:
        unreadable = get_config_manager().has_unreadable_key(spec.id)
        return ModelCatalogResponse(
            models=fallback,
            key_state="unreadable" if unreadable else "unverified",
            error="No API key is saved.",
        )

    entry = await catalog_call("get", spec.id, base_url)
    if entry is not None and not refresh and catalog.is_fresh(entry):
        return _listed_response(entry.listing, entry.fetched_at, saved_model, entry.key_state)

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
                entry.listing, entry.fetched_at, saved_model, entry.key_state, str(exc)
            )
        return ModelCatalogResponse(
            models=fallback,
            key_state=entry.key_state if entry is not None else "unverified",
            error=str(exc),
        )

    now = now_ms()
    await catalog_call("put", spec.id, base_url, listing, now=now)
    return _listed_response(listing, now, saved_model, "valid")


# ---------------------------------------------------------------------------
# changing a provider's settings, shared with PUT /config
# ---------------------------------------------------------------------------


def endpoint_change_refusal(
    current: Dict[str, Any],
    spec: ProviderSpec,
    api_key: Optional[str],
    base_url: Optional[str],
) -> Optional[str]:
    """Why this update may not change the endpoint, or `None` if it may.

    A saved key is only ever sent to the endpoint it was saved for. `GET
    /config` never hands a key out; without this, anything able to change the
    endpoint could point it at a server of its own and read the key off the
    next request (#32). A key the manager is preserving unread counts as
    saved: it decrypts again once the keystore is reachable, and would then go
    to whatever endpoint was set meanwhile.

    Callers check every provider in a request before applying anything, so a
    refusal leaves the settings and the preserved-key records untouched.
    """
    if api_key is not None or base_url is None:
        return None
    section = current["providers"][spec.id]
    if (base_url or None) == section.get("base_url"):
        return None
    if section.get("api_key") or get_config_manager().has_unreadable_key(spec.id):
        return f"Enter the {spec.label} API key again to change its endpoint."
    return None


@dataclass
class KeyChange:
    # A key was set or cleared: it is now the only key, and a ciphertext the
    # manager preserves unread must not come back on the save.
    rekeyed: bool = False
    # A key or the endpoint changed: the catalog's rows describe the old pair.
    stale_catalog: bool = False
    # A non-empty key was set.
    new_key: bool = False


def apply_key_and_endpoint(
    current: Dict[str, Any],
    spec: ProviderSpec,
    api_key: Optional[str],
    base_url: Optional[str],
) -> KeyChange:
    """Set the key (`""` clears it) and the endpoint (`""` is the provider's
    own) in a settings dump; `None` leaves either alone. Only after
    `endpoint_change_refusal`."""
    section = current["providers"][spec.id]
    change = KeyChange()
    if api_key is not None:
        section["api_key"] = api_key or None
        change.rekeyed = change.stale_catalog = True
        change.new_key = bool(api_key)
    if base_url is not None and (base_url or None) != section.get("base_url"):
        section["base_url"] = base_url or None
        change.stale_catalog = True
    return change


def _validation_detail(exc: ValidationError) -> str:
    error = exc.errors()[0]
    where = ".".join(str(part) for part in error.get("loc", ()))
    return f"{where}: {error.get('msg')}" if where else str(error.get("msg"))


async def save_settings(
    current: Dict[str, Any],
    rekeyed: Iterable[str] = (),
    stale_catalog: Iterable[str] = (),
) -> AppSettings:
    """Validate a settings dump, save it, and make it the settings.

    A value the registry refuses for its provider (a temperature over its
    ceiling) is a 422 before anything changes. Preserved ciphertext is
    forgotten only for a key actually being replaced, and only once the new
    settings are known to be valid. The catalog is dropped after the save,
    never before: a refused save must leave it describing the key that is
    still in the file.
    """
    mgr = get_config_manager()
    try:
        new_settings = AppSettings(**current)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_detail(exc)) from None
    for provider_id in rekeyed:
        mgr.forget_unreadable_key(provider_id)
    # Off the loop thread: `save_settings` fsyncs and rewrites the backup, and
    # this loop is also carrying any in-flight translation's SSE stream.
    if not await asyncio.to_thread(mgr.save_settings, new_settings):
        # Nothing was written, so a key dropped from the preserved-ciphertext
        # records above is still in the file. Re-read it, or a later unrelated
        # save would blank the value this request failed to replace.
        await asyncio.to_thread(mgr.load_settings)
        raise HTTPException(status_code=500, detail="Failed to save settings")
    mgr._settings = new_settings  # refresh cached singleton
    for provider_id in stale_catalog:
        await catalog_call("invalidate", provider_id)
    return new_settings


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


async def _provider_info(spec: ProviderSpec, settings: AppSettings) -> ProviderInfo:
    saved = settings.providers[spec.id]
    has_key = bool(saved.api_key) if spec.requires_key else False
    # A keyless server's row is filled by listing it, like a key's.
    listable = has_key or (not spec.requires_key and spec.lister is not None)
    entry = await catalog_call("get", spec.id, saved.base_url) if listable else None
    if spec.requires_key and get_config_manager().has_unreadable_key(spec.id):
        key_state: KeyState = "unreadable"
    elif entry is not None:
        key_state = entry.key_state
    else:
        key_state = "unverified"
    return ProviderInfo(
        id=TranslationService(spec.id),
        label=spec.label,
        short_label=spec.short_label,
        description=spec.description,
        protocol=spec.protocol,
        requires_key=spec.requires_key,
        takes_endpoint=spec.takes_endpoint,
        default_base_url=spec.default_base_url,
        endpoint_hint=spec.endpoint_hint,
        default_model=spec.default_model,
        suggested_models=list(spec.suggested_models),
        model_is_fixed=spec.model_is_fixed,
        signup_url=spec.signup_url,
        is_llm=spec.is_llm,
        priority=spec.priority,
        has_key=has_key,
        base_url=saved.base_url,
        key_state=key_state,
        last_verified_at=_iso(entry.verified_at) if entry else None,
        catalog_fetched_at=_iso(entry.fetched_at) if entry else None,
        catalog_fresh=bool(entry and catalog.is_fresh(entry)),
        model=settings.model_for(spec.id),
        enabled_models=list(saved.enabled_models),
        temperature=saved.temperature,
        max_tokens=saved.max_tokens,
        max_qps=saved.max_qps,
    )


@router.get("", response_model=ProvidersResponse)
async def list_providers() -> ProvidersResponse:
    """Every provider, with where its key stands. Reads only; never lists."""
    settings = get_settings()
    return ProvidersResponse(
        providers=[await _provider_info(provider(spec.id), settings) for spec in PROVIDERS]
    )


@router.put("/{provider_id}", response_model=ProviderInfo)
async def update_provider(
    provider_id: ProviderId, payload: ProviderUpdateRequest
) -> ProviderInfo:
    """Save one provider's key, endpoint, models and parameters.

    Saves without checking the key with the provider: `POST .../verify` is
    that step, and "Save anyway" has to exist. Unlike `PUT /config`, a first
    key saved here does not move translation off Argos by itself; the caller
    chooses with `PUT /config`'s `translation_model`.
    """
    spec = provider(provider_id.value)
    if payload.api_key is not None and not spec.requires_key:
        raise HTTPException(status_code=422, detail=f"{spec.label} takes no API key.")
    if payload.base_url and not spec.takes_endpoint:
        raise HTTPException(
            status_code=422, detail=f"{spec.label} takes no endpoint of its own."
        )
    current = get_config_manager().settings.model_dump()
    refusal = endpoint_change_refusal(current, spec, payload.api_key, payload.base_url)
    if refusal:
        raise HTTPException(status_code=422, detail=refusal)
    change = apply_key_and_endpoint(current, spec, payload.api_key, payload.base_url)

    section = current["providers"][spec.id]
    # `null` leaves these two alone; the two that can be unset take `null`
    # sent explicitly as "back to the built-in default".
    for field in ("enabled_models", "temperature"):
        value = getattr(payload, field)
        if value is not None:
            section[field] = value
    for field in ("max_tokens", "max_qps"):
        if field in payload.model_fields_set:
            section[field] = getattr(payload, field)

    settings = await save_settings(
        current,
        rekeyed=[spec.id] if change.rekeyed else [],
        stale_catalog=[spec.id] if change.stale_catalog else [],
    )
    return await _provider_info(spec, settings)


@router.delete("/{provider_id}/key", response_model=ProviderInfo)
async def delete_provider_key(provider_id: ProviderId) -> ProviderInfo:
    """Forget the saved key, a preserved unreadable one included. The
    endpoint stays; a key entered later goes to it."""
    spec = provider(provider_id.value)
    if not spec.requires_key:
        raise HTTPException(status_code=422, detail=f"{spec.label} has no API key.")
    current = get_config_manager().settings.model_dump()
    current["providers"][spec.id]["api_key"] = None
    settings = await save_settings(current, rekeyed=[spec.id], stale_catalog=[spec.id])
    return await _provider_info(spec, settings)


@router.get("/{provider_id}/models", response_model=ModelCatalogResponse)
async def provider_models(
    provider_id: ProviderId, refresh: bool = False
) -> ModelCatalogResponse:
    return await catalog_for(provider_id, refresh)


@router.post("/{provider_id}/verify", response_model=VerifyResponse)
async def verify_provider(
    provider_id: ProviderId, payload: VerifyRequest
) -> VerifyResponse:
    return await verify(provider_id, payload.api_key, payload.base_url, payload.model)
