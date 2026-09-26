"""The discovered-model catalog (`providers/catalog.py`, #84).

A listing of the models a key can use is disposable — it can always be asked
for again — so it lives in the cache tier, keyed by `(provider_id, base_url)`,
not in `pdfusion.db`. Alongside it the catalog remembers what the last check
said about the key: a listing means `valid`, a refusal (401/403) means
`invalid` with nothing to offer.

Every test builds its own catalog under `tmp_path`, and every timestamp is
passed explicitly, so the 24 h TTL is exercised without sleeping.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

import pytest

from desktop_pdf_translator.providers import catalog as catalog_module
from desktop_pdf_translator.providers import listing
from desktop_pdf_translator.providers.catalog import (
    TTL_MS,
    CatalogEntry,
    ModelCatalog,
    is_fresh,
)
from desktop_pdf_translator.providers.listing import ListedModel, Listing

# An arbitrary fixed instant (2026, Unix ms): tests never read the clock.
T0 = 1_780_000_000_000

LOCAL_BASE_URL = "http://localhost:11434/v1"

FULL_LISTING = Listing(
    models=(
        ListedModel(
            id="gpt-4o",
            display_name="GPT-4o",
            context_tokens=128_000,
            output_tokens=16_384,
            created_at=1_715_367_049_000,
        ),
        ListedModel(id="gpt-4o-mini"),
        ListedModel(
            id="o3",
            display_name="o3",
            context_tokens=200_000,
            output_tokens=100_000,
            created_at=1_744_225_308_000,
        ),
    ),
    hidden=(
        ListedModel(id="text-embedding-3-small", created_at=1_705_948_997_000),
        ListedModel(id="dall-e-3", display_name="DALL-E 3"),
    ),
)

OTHER_LISTING = Listing(models=(ListedModel(id="llama3.1:8b"),))


@pytest.fixture
def catalog(tmp_path: Path) -> ModelCatalog:
    return ModelCatalog(cache_dir=tmp_path / "model_catalog")


def _user_version(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# put / get
# ---------------------------------------------------------------------------


def test_a_stored_listing_comes_back_whole(catalog: ModelCatalog):
    """The picker renders every field it was given, and the endpoint's order
    (with the hidden models kept apart) is what the user sees — so nothing may
    be dropped, coerced or reordered on the way through the store."""
    catalog.put("openai", None, FULL_LISTING, now=T0)

    entry = catalog.get("openai", None)

    assert entry == CatalogEntry(
        provider_id="openai",
        base_url=None,
        listing=FULL_LISTING,
        fetched_at=T0,
        key_state="valid",
        verified_at=T0,
    )


def test_an_unseen_endpoint_is_a_miss(catalog: ModelCatalog):
    assert catalog.get("openai", None) is None


def test_each_base_url_has_its_own_row(catalog: ModelCatalog):
    """One provider id serves both its own endpoint and any OpenAI-compatible
    server the user points it at; their model lists have nothing in common."""
    catalog.put("openai", None, FULL_LISTING, now=T0)
    catalog.put("openai", LOCAL_BASE_URL, OTHER_LISTING, now=T0 + 5)

    own = catalog.get("openai", None)
    local = catalog.get("openai", LOCAL_BASE_URL)

    assert own is not None and own.listing == FULL_LISTING
    assert own.fetched_at == T0
    assert local is not None and local.listing == OTHER_LISTING
    assert local.base_url == LOCAL_BASE_URL
    assert local.fetched_at == T0 + 5
    assert catalog.get("openai", "http://localhost:8080/v1") is None


def test_a_later_listing_replaces_the_earlier_one(catalog: ModelCatalog):
    """A refresh supersedes what was there; models the endpoint stopped
    listing must disappear rather than accumulate."""
    catalog.put("openai", None, FULL_LISTING, now=T0)
    catalog.put("openai", None, OTHER_LISTING, now=T0 + 1_000)

    entry = catalog.get("openai", None)

    assert entry is not None
    assert entry.listing == OTHER_LISTING
    assert entry.fetched_at == T0 + 1_000
    assert entry.verified_at == T0 + 1_000
    assert entry.key_state == "valid"


# ---------------------------------------------------------------------------
# freshness
# ---------------------------------------------------------------------------


def test_a_listing_goes_stale_exactly_one_ttl_after_it_was_fetched(
    catalog: ModelCatalog,
):
    catalog.put("openai", None, FULL_LISTING, now=T0)
    entry = catalog.get("openai", None)
    assert entry is not None

    assert is_fresh(entry, T0) is True
    assert is_fresh(entry, T0 + TTL_MS - 1) is True
    assert is_fresh(entry, T0 + TTL_MS) is False


def test_a_refused_key_is_never_fresh(catalog: ModelCatalog):
    """There is no list to serve, so a refused key must always send the caller
    back to the endpoint — however recently the refusal was recorded."""
    catalog.mark_invalid("openai", None, now=T0)
    entry = catalog.get("openai", None)
    assert entry is not None

    assert is_fresh(entry, T0) is False


# ---------------------------------------------------------------------------
# key state and invalidation
# ---------------------------------------------------------------------------


def test_a_refused_key_is_recorded_invalid_and_its_list_dropped(
    catalog: ModelCatalog,
):
    """A list fetched with a key that has since been refused would offer
    models the user can no longer reach."""
    catalog.put("openai", None, FULL_LISTING, now=T0)

    catalog.mark_invalid("openai", None, now=T0 + 60_000)

    assert catalog.get("openai", None) == CatalogEntry(
        provider_id="openai",
        base_url=None,
        listing=None,
        fetched_at=None,
        key_state="invalid",
        verified_at=T0 + 60_000,
    )


def test_invalidating_a_provider_drops_every_endpoint_of_it_and_nothing_else(
    catalog: ModelCatalog,
):
    """A key change is the only signal the catalog gets (it never sees the
    key), and the old key's lists are wrong for every base_url it was used
    with."""
    catalog.put("openai", None, FULL_LISTING, now=T0)
    catalog.mark_invalid("openai", LOCAL_BASE_URL, now=T0)
    catalog.put("gemini", None, OTHER_LISTING, now=T0)

    assert catalog.invalidate("openai") == 2

    assert catalog.get("openai", None) is None
    assert catalog.get("openai", LOCAL_BASE_URL) is None
    gemini = catalog.get("gemini", None)
    assert gemini is not None and gemini.listing == OTHER_LISTING
    assert catalog.invalidate("openai") == 0


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------


def test_listings_survive_a_restart(tmp_path: Path):
    ModelCatalog(cache_dir=tmp_path / "model_catalog").put(
        "anthropic", None, FULL_LISTING, now=T0
    )

    entry = ModelCatalog(cache_dir=tmp_path / "model_catalog").get("anthropic", None)

    assert entry is not None
    assert entry.listing == FULL_LISTING
    assert entry.key_state == "valid"


def test_a_new_store_is_migrated_to_schema_version_one(catalog: ModelCatalog):
    catalog.put("openai", None, FULL_LISTING, now=T0)

    assert _user_version(catalog.db_path) == 1


def test_a_store_from_a_newer_build_is_set_aside_for_an_empty_one(tmp_path: Path):
    """Caches are disposable: a file this build can't read must not stop model
    discovery, but it isn't deleted either — a rolled-back build may return."""
    catalog = ModelCatalog(cache_dir=tmp_path / "model_catalog")
    catalog.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(catalog.db_path)
    conn.execute("CREATE TABLE from_the_future (id INTEGER)")
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()

    assert catalog.get("openai", None) is None
    catalog.put("openai", None, OTHER_LISTING, now=T0)
    entry = catalog.get("openai", None)

    assert entry is not None and entry.listing == OTHER_LISTING
    assert _user_version(catalog.db_path) == 1
    own_files = {
        catalog.db_path.name + suffix for suffix in ("", "-wal", "-shm", "-journal")
    }
    set_aside = [
        p
        for p in catalog.db_path.parent.iterdir()
        if p.name.startswith(catalog.db_path.name) and p.name not in own_files
    ]
    assert set_aside, "the newer build's file should be kept alongside"


def test_no_column_could_hold_key_material(catalog: ModelCatalog):
    """Invalidation is an explicit call precisely so that nothing derived from
    the key — not the key, not a hash, not a fingerprint — sits in a cache
    file anyone can copy."""
    catalog.put("openai", None, FULL_LISTING, now=T0)

    conn = sqlite3.connect(catalog.db_path)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        columns = {
            f"{table}.{col[1]}".lower()
            for table in tables
            for col in conn.execute(f'PRAGMA table_info("{table}")')
        }
    finally:
        conn.close()

    assert tables, "the store should have created its table"
    keyish = ("api_key", "apikey", "key_hash", "keyhash", "fingerprint", "secret")
    offending = sorted(c for c in columns if any(k in c for k in keyish))
    assert offending == []


def test_building_a_catalog_touches_no_database(tmp_path: Path):
    """The store is built on the sidecar's event loop, where blocking I/O
    freezes every stream; the schema is migrated on first use instead."""
    catalog = ModelCatalog(cache_dir=tmp_path / "model_catalog")

    assert catalog.db_path.parent == tmp_path / "model_catalog"
    assert not catalog.db_path.exists()


# ---------------------------------------------------------------------------
# list_models: the registry's lister, off the loop, with failures sorted
# ---------------------------------------------------------------------------


class _StatusError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def _patch_openai_lister(monkeypatch: pytest.MonkeyPatch, behaviour):
    calls = []

    def fake(api_key, base_url, **_):
        calls.append((api_key, base_url))
        return behaviour()

    monkeypatch.setattr(listing, "list_openai_models", fake)
    return calls


def test_list_models_asks_the_providers_lister(monkeypatch: pytest.MonkeyPatch):
    calls = _patch_openai_lister(monkeypatch, lambda: OTHER_LISTING)

    listing = asyncio.run(catalog_module.list_models("openai", "ollama", LOCAL_BASE_URL))

    assert listing == OTHER_LISTING
    assert calls == [("ollama", LOCAL_BASE_URL)]


@pytest.mark.parametrize(
    "error",
    [
        _StatusError("Incorrect API key provided", 401),
        _StatusError("Forbidden", 403),
        # What Gemini actually answers a bad key with: 400, not 401.
        Exception("400 INVALID_ARGUMENT. API key not valid. Please pass a valid API key."),
    ],
)
def test_a_refused_key_is_key_rejected(monkeypatch: pytest.MonkeyPatch, error):
    def refuse():
        raise error

    _patch_openai_lister(monkeypatch, refuse)

    with pytest.raises(catalog_module.KeyRejected):
        asyncio.run(catalog_module.list_models("openai", "sk-bad", None))


def test_a_server_that_is_down_is_listing_failed(monkeypatch: pytest.MonkeyPatch):
    def down():
        raise ConnectionError("Connection refused")

    _patch_openai_lister(monkeypatch, down)

    with pytest.raises(catalog_module.ListingFailed, match="Connection refused"):
        asyncio.run(catalog_module.list_models("openai", "ollama", LOCAL_BASE_URL))


def test_a_listing_past_the_deadline_is_listing_failed(monkeypatch: pytest.MonkeyPatch):
    # `asyncio.run` joins the worker thread before returning, so the stall is
    # kept short rather than released from the test.
    _patch_openai_lister(monkeypatch, lambda: time.sleep(0.3) or OTHER_LISTING)
    monkeypatch.setattr(catalog_module, "PROBE_TIMEOUT_S", 0.05)

    with pytest.raises(catalog_module.ListingFailed, match="[Tt]imed out"):
        asyncio.run(catalog_module.list_models("openai", "ollama", LOCAL_BASE_URL))
