"""`/providers`: each key's state and the models it can use, found by listing (#84).

Verifying a key and discovering models never generate text: both ask the
endpoint which models the key can use, and a 401/403 there means a bad key.
What a listing said is kept in a disposable catalog keyed by
`(provider_id, base_url)`.

Nothing here reaches a provider. `catalog.list_models` is a recorder
(`provider_fakes.FakeLister`), the catalog lives under `tmp_path`, and settings
live in a `ConfigManager` under `tmp_path`, as in `test_config_api.py`. The
#32 rule — a saved key is only ever sent to the endpoint it was saved for —
holds for Verify exactly as it did for `/config/validate`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from desktop_pdf_translator.api import auth
from desktop_pdf_translator.api.routes import config as config_routes
from desktop_pdf_translator.api.routes import providers as providers_routes
from desktop_pdf_translator.config.manager import ConfigManager
from desktop_pdf_translator.providers import catalog
from desktop_pdf_translator.providers.catalog import TTL_MS, ModelCatalog
from desktop_pdf_translator.providers.listing import ListedModel, Listing
from desktop_pdf_translator.providers.registry import PROVIDERS, provider
from desktop_pdf_translator.storage.sqlite import ms_to_iso, now_ms
from desktop_pdf_translator.utils.encryption import KEYSTORE_PREFIX

from provider_fakes import (
    ATTACKER,
    DEFAULT_LISTING,
    OLLAMA,
    FakeLister,
    GenerationGuard,
    ids_and_sources,
    install_catalog,
    install_factory_guard,
    install_full_guard,
    install_lister,
    listed,
    seed,
)

TOKEN = "test-token-for-providers-api"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
KEY = "sk-test-0123456789abcdef"

ANTHROPIC_LISTING = Listing(
    models=(
        ListedModel(id="claude-sonnet-4-6", display_name="Claude Sonnet 4.6"),
        ListedModel(id="claude-haiku-4-5-20251001", display_name="Claude Haiku 4.5"),
    ),
    hidden=(ListedModel(id="claude-embed-v1"),),
)
OTHER_LISTING = Listing(models=(ListedModel(id="llama3.1:8b"),))


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "_TOKEN", TOKEN)


@pytest.fixture
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ConfigManager:
    """A manager over a throwaway dir, with the developer's own credentials out
    of the way. The providers routes read settings through the same two names
    the config routes do, so both modules are patched."""
    monkeypatch.setattr(ConfigManager, "_load_dotenv", lambda self: None)
    for service in ("OPENAI", "GEMINI", "ANTHROPIC"):
        monkeypatch.delenv(f"{service}_API_KEY", raising=False)
        monkeypatch.delenv(f"{service}_MODEL", raising=False)
    manager = ConfigManager(config_dir=tmp_path / "PDFusion")
    for module in (config_routes, providers_routes):
        monkeypatch.setattr(module, "get_config_manager", lambda: manager, raising=False)
        monkeypatch.setattr(module, "get_settings", lambda: manager.settings, raising=False)
    return manager


@pytest.fixture
def lister(monkeypatch: pytest.MonkeyPatch) -> FakeLister:
    return install_lister(monkeypatch)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModelCatalog:
    return install_catalog(monkeypatch, tmp_path)


@pytest.fixture
def factory_guard(monkeypatch: pytest.MonkeyPatch) -> GenerationGuard:
    return install_factory_guard(monkeypatch)


@pytest.fixture
def client(
    manager: ConfigManager,
    lister: FakeLister,
    store: ModelCatalog,
    factory_guard: GenerationGuard,
) -> TestClient:
    app = FastAPI()
    app.include_router(config_routes.router)
    app.include_router(providers_routes.router)
    return TestClient(app)


@pytest.fixture
def unreadable_key(manager: ConfigManager) -> str:
    """A stored openai key this process cannot decrypt — a `keystore:` value
    with no keystore to open it (conftest's `_no_real_keystore`)."""
    stored = KEYSTORE_PREFIX + "c3RvcmVkLWNpcGhlcnRleHQ="
    manager.config_file.write_text(f'[openai]\napi_key = "{stored}"\n', encoding="utf-8")
    manager._settings = None
    assert manager.settings.openai.api_key is None
    assert manager.has_unreadable_key("openai")
    return stored


def providers(client: TestClient) -> dict:
    response = client.get("/providers", headers=AUTH)
    assert response.status_code == 200
    return {entry["id"]: entry for entry in response.json()["providers"]}


def models(client: TestClient, provider_id: str, refresh: bool = False):
    query = "?refresh=true" if refresh else ""
    return client.get(f"/providers/{provider_id}/models{query}", headers=AUTH)


def verify(client: TestClient, provider_id: str, body: dict):
    return client.post(f"/providers/{provider_id}/verify", json=body, headers=AUTH)


def suggested(provider_id: str):
    return [(model, "suggested") for model in provider(provider_id).suggested_models]


def fresh() -> int:
    """A fetch time well inside the TTL."""
    return now_ms() - 60_000


def stale() -> int:
    return now_ms() - TTL_MS - 60_000


# ---------------------------------------------------------------------------
# GET /providers
# ---------------------------------------------------------------------------


def test_every_provider_is_listed_in_registry_order_with_its_spec(client: TestClient):
    response = client.get("/providers", headers=AUTH)

    assert response.status_code == 200
    entries = response.json()["providers"]
    assert [entry["id"] for entry in entries] == ["openai", "gemini", "anthropic", "argos"]
    for entry, spec in zip(entries, PROVIDERS):
        assert entry["label"] == spec.label
        assert entry["short_label"] == spec.short_label
        assert entry["protocol"] == spec.protocol
        assert entry["requires_key"] is spec.requires_key
        assert entry["takes_endpoint"] is spec.takes_endpoint
        assert entry["default_base_url"] == spec.default_base_url
        assert entry["default_model"] == spec.default_model
        assert entry["suggested_models"] == list(spec.suggested_models)
        assert entry["model_is_fixed"] is spec.model_is_fixed
        assert entry["signup_url"] == spec.signup_url


def test_key_and_endpoint_come_from_the_saved_settings(
    client: TestClient, manager: ConfigManager
):
    seed(manager, openai={"api_key": "ollama", "base_url": OLLAMA})

    entries = providers(client)

    assert (entries["openai"]["has_key"], entries["openai"]["base_url"]) == (True, OLLAMA)
    assert (entries["gemini"]["has_key"], entries["gemini"]["base_url"]) == (False, None)
    assert entries["anthropic"]["has_key"] is False


def test_without_keys_every_provider_is_unverified(client: TestClient):
    for entry in providers(client).values():
        assert entry["key_state"] == "unverified"
        assert entry["last_verified_at"] is None
        assert entry["catalog_fetched_at"] is None
        assert entry["catalog_fresh"] is False


def test_a_saved_key_never_listed_is_unverified(client: TestClient, manager: ConfigManager):
    seed(manager, openai={"api_key": KEY})

    assert providers(client)["openai"]["key_state"] == "unverified"


def test_a_key_that_lists_is_valid(
    client: TestClient, manager: ConfigManager, store: ModelCatalog
):
    seed(manager, openai={"api_key": KEY})
    at = fresh()
    store.put("openai", None, DEFAULT_LISTING, now=at)

    entry = providers(client)["openai"]

    assert entry["key_state"] == "valid"
    assert entry["last_verified_at"] == ms_to_iso(at)
    assert entry["catalog_fetched_at"] == ms_to_iso(at)
    assert entry["catalog_fresh"] is True


def test_a_refused_key_is_invalid(
    client: TestClient, manager: ConfigManager, store: ModelCatalog
):
    seed(manager, anthropic={"api_key": KEY})
    at = fresh()
    store.mark_invalid("anthropic", None, now=at)

    entry = providers(client)["anthropic"]

    assert entry["key_state"] == "invalid"
    assert entry["last_verified_at"] == ms_to_iso(at)
    assert entry["catalog_fetched_at"] is None
    assert entry["catalog_fresh"] is False


def test_a_listing_older_than_the_ttl_is_not_fresh(
    client: TestClient, manager: ConfigManager, store: ModelCatalog
):
    seed(manager, gemini={"api_key": KEY})
    at = stale()
    store.put("gemini", None, DEFAULT_LISTING, now=at)

    entry = providers(client)["gemini"]

    assert entry["key_state"] == "valid"
    assert entry["catalog_fetched_at"] == ms_to_iso(at)
    assert entry["catalog_fresh"] is False


def test_the_state_is_read_for_the_saved_endpoint(
    client: TestClient, manager: ConfigManager, store: ModelCatalog
):
    """An Ollama endpoint and the provider's own are different rows: a key
    refused by OpenAI says nothing about the local server."""
    seed(manager, openai={"api_key": "ollama", "base_url": OLLAMA})
    store.mark_invalid("openai", None)
    assert providers(client)["openai"]["key_state"] == "unverified"

    store.put("openai", OLLAMA, OTHER_LISTING)

    assert providers(client)["openai"]["key_state"] == "valid"


def test_a_key_that_could_not_be_read_is_unreadable(
    client: TestClient, store: ModelCatalog, unreadable_key: str
):
    """Whatever the catalog last said, the key the app would send is not the
    one that was checked — there is none it can read."""
    store.put("openai", None, DEFAULT_LISTING)

    assert providers(client)["openai"]["key_state"] == "unreadable"


def test_argos_is_unverified(client: TestClient):
    assert providers(client)["argos"]["key_state"] == "unverified"


def test_listing_providers_never_lists_models(
    client: TestClient, manager: ConfigManager, lister: FakeLister
):
    seed(manager, openai={"api_key": KEY}, gemini={"api_key": KEY}, anthropic={"api_key": KEY})

    providers(client)

    assert lister.calls == []


# ---------------------------------------------------------------------------
# GET /providers/{id}/models
# ---------------------------------------------------------------------------


def test_argos_offers_only_its_fixed_model(client: TestClient, lister: FakeLister):
    response = models(client, "argos")

    assert response.status_code == 200
    body = response.json()
    assert ids_and_sources(body["models"]) == [("argostranslate", "suggested")]
    assert body["error"] is None
    assert lister.calls == []


def test_without_a_key_the_saved_model_leads_the_suggestions(
    client: TestClient, manager: ConfigManager, lister: FakeLister
):
    seed(manager, gemini={"model": "gemini-custom-exp"})

    body = models(client, "gemini").json()

    assert ids_and_sources(body["models"]) == [("gemini-custom-exp", "saved")] + suggested(
        "gemini"
    )
    assert "API key" in body["error"]
    assert body["key_state"] == "unverified"
    assert lister.calls == []


def test_a_saved_model_among_the_suggestions_is_not_repeated(
    client: TestClient, lister: FakeLister
):
    """The default is the first suggestion (`test_provider_registry.py`)."""
    body = models(client, "anthropic").json()

    assert ids_and_sources(body["models"]) == suggested("anthropic")
    assert lister.calls == []


def test_a_custom_endpoint_without_a_key_offers_only_the_saved_model(
    client: TestClient, manager: ConfigManager, lister: FakeLister
):
    """OpenAI's suggestions mean nothing to a local server."""
    seed(manager, openai={"base_url": OLLAMA, "model": "mistral-nemo:12b"})

    body = models(client, "openai").json()

    assert ids_and_sources(body["models"]) == [("mistral-nemo:12b", "saved")]
    assert body["error"]
    assert lister.calls == []


def test_a_fresh_catalog_entry_is_served_without_listing(
    client: TestClient, manager: ConfigManager, store: ModelCatalog, lister: FakeLister
):
    seed(manager, openai={"api_key": KEY})
    at = fresh()
    store.put("openai", None, DEFAULT_LISTING, now=at)

    body = models(client, "openai").json()

    assert lister.calls == []
    assert ids_and_sources(body["models"]) == listed(DEFAULT_LISTING.models)
    assert ids_and_sources(body["hidden"]) == listed(DEFAULT_LISTING.hidden)
    assert body["key_state"] == "valid"
    assert body["fetched_at"] == ms_to_iso(at)
    assert body["error"] is None


def test_refresh_lists_even_with_a_fresh_entry(
    client: TestClient, manager: ConfigManager, store: ModelCatalog, lister: FakeLister
):
    seed(manager, openai={"api_key": KEY})
    store.put("openai", None, OTHER_LISTING, now=fresh())

    body = models(client, "openai", refresh=True).json()

    assert lister.calls == [("openai", KEY, None)]
    assert ids_and_sources(body["models"]) == listed(DEFAULT_LISTING.models)


def test_a_stale_entry_is_listed_again(
    client: TestClient, manager: ConfigManager, store: ModelCatalog, lister: FakeLister
):
    seed(manager, openai={"api_key": KEY})
    store.put("openai", None, OTHER_LISTING, now=stale())

    body = models(client, "openai").json()

    assert lister.calls == [("openai", KEY, None)]
    assert ids_and_sources(body["models"]) == listed(DEFAULT_LISTING.models)


def test_the_saved_key_is_listed_at_the_saved_endpoint(
    client: TestClient, manager: ConfigManager, lister: FakeLister
):
    seed(manager, openai={"api_key": "ollama", "base_url": OLLAMA, "model": "qwen3"})

    response = models(client, "openai")

    assert response.status_code == 200
    body = response.json()
    assert lister.calls == [("openai", "ollama", OLLAMA)]
    assert ids_and_sources(body["models"]) == listed(DEFAULT_LISTING.models)
    assert ids_and_sources(body["hidden"]) == listed(DEFAULT_LISTING.hidden)
    assert body["key_state"] == "valid"
    assert body["fetched_at"] is not None
    assert body["error"] is None


def test_a_listed_record_carries_what_the_endpoint_said(
    client: TestClient, manager: ConfigManager
):
    seed(manager, openai={"api_key": KEY})

    first = models(client, "openai").json()["models"][0]

    assert first["id"] == "gpt-4.1"
    assert first["display_name"] == "GPT-4.1"
    assert first["context_tokens"] == 1_047_576
    assert first["output_tokens"] == 32_768


def test_gemini_is_listed_with_no_endpoint(
    client: TestClient, manager: ConfigManager, lister: FakeLister
):
    seed(manager, gemini={"api_key": KEY})

    assert models(client, "gemini").status_code == 200
    assert lister.calls == [("gemini", KEY, None)]


def test_a_successful_listing_is_stored(
    client: TestClient, manager: ConfigManager, store: ModelCatalog
):
    seed(manager, openai={"api_key": "ollama", "base_url": OLLAMA})

    models(client, "openai")

    entry = store.get("openai", OLLAMA)
    assert entry is not None
    assert entry.key_state == "valid"
    assert entry.listing == DEFAULT_LISTING
    assert providers(client)["openai"]["key_state"] == "valid"


@pytest.mark.parametrize(
    "saved_model",
    [
        "my-finetune",
        # An alias is still not the id the endpoint named, so the name the user
        # typed stays on offer as typed.
        "llama3.2",
    ],
)
def test_a_saved_model_the_listing_does_not_name_leads_it(
    client: TestClient, manager: ConfigManager, saved_model: str
):
    seed(manager, openai={"api_key": KEY, "model": saved_model})

    body = models(client, "openai").json()

    assert ids_and_sources(body["models"]) == [(saved_model, "saved")] + listed(
        DEFAULT_LISTING.models
    )


def test_a_saved_model_the_listing_names_is_not_repeated(
    client: TestClient, manager: ConfigManager
):
    seed(manager, openai={"api_key": KEY, "model": "llama3.2:3b"})

    body = models(client, "openai").json()

    assert ids_and_sources(body["models"]) == listed(DEFAULT_LISTING.models)


def test_a_refused_key_is_recorded_invalid(
    client: TestClient, manager: ConfigManager, store: ModelCatalog, lister: FakeLister
):
    seed(manager, openai={"api_key": KEY, "model": "my-finetune"})
    lister.result = catalog.KeyRejected("401 Unauthorized")

    response = models(client, "openai")

    assert response.status_code == 200
    body = response.json()
    assert body["error"]
    assert body["key_state"] == "invalid"
    assert ids_and_sources(body["models"]) == [("my-finetune", "saved")] + suggested("openai")
    entry = store.get("openai", None)
    assert entry is not None and entry.key_state == "invalid"


def test_a_refused_key_on_a_custom_endpoint_offers_only_the_saved_model(
    client: TestClient, manager: ConfigManager, lister: FakeLister
):
    seed(manager, anthropic={"api_key": "ollama", "base_url": "http://localhost:11434", "model": "qwen3"})
    lister.result = catalog.KeyRejected("403 Forbidden")

    body = models(client, "anthropic").json()

    assert body["key_state"] == "invalid"
    assert ids_and_sources(body["models"]) == [("qwen3", "saved")]


def test_a_server_that_is_down_is_an_error_not_a_failure(
    client: TestClient, manager: ConfigManager, lister: FakeLister
):
    seed(manager, openai={"api_key": "ollama", "base_url": OLLAMA, "model": "qwen3"})
    lister.result = catalog.ListingFailed("Connection refused")

    response = models(client, "openai")

    assert response.status_code == 200
    assert response.json()["error"] == "Connection refused"
    assert ids_and_sources(response.json()["models"]) == [("qwen3", "saved")]


def test_a_down_server_on_the_providers_own_endpoint_offers_the_suggestions(
    client: TestClient, manager: ConfigManager, lister: FakeLister
):
    seed(manager, gemini={"api_key": KEY})
    lister.result = catalog.ListingFailed("Connection refused")

    body = models(client, "gemini").json()

    assert body["error"] == "Connection refused"
    assert ids_and_sources(body["models"]) == suggested("gemini")


def test_a_stale_listing_beats_none_when_the_server_is_down(
    client: TestClient, manager: ConfigManager, store: ModelCatalog, lister: FakeLister
):
    seed(manager, openai={"api_key": "ollama", "base_url": OLLAMA, "model": "llama3.1:8b"})
    store.put("openai", OLLAMA, OTHER_LISTING, now=stale())
    lister.result = catalog.ListingFailed("Connection refused")

    body = models(client, "openai").json()

    assert lister.calls == [("openai", "ollama", OLLAMA)]
    assert body["error"] == "Connection refused"
    assert ids_and_sources(body["models"]) == [("llama3.1:8b", "listed")]


def test_an_unknown_provider_has_no_models(client: TestClient):
    assert models(client, "mistral").status_code == 422


# ---------------------------------------------------------------------------
# POST /providers/{id}/verify
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"api_key": "sk-typed", "base_url": OLLAMA, "model": "qwen3"},
        {},
    ],
)
def test_verify_saves_nothing(client: TestClient, manager: ConfigManager, body: dict):
    seed(manager, openai={"api_key": KEY})
    before = manager.config_file.read_bytes()

    assert verify(client, "openai", body).status_code == 200

    assert manager.config_file.read_bytes() == before


def test_verify_lists_a_typed_key_at_the_named_endpoint(
    client: TestClient, lister: FakeLister
):
    response = verify(
        client, "anthropic", {"api_key": "ollama", "base_url": "http://localhost:11434"}
    )

    assert response.status_code == 200
    assert lister.calls == [("anthropic", "ollama", "http://localhost:11434")]


def test_a_typed_key_with_no_endpoint_goes_to_the_saved_endpoint(
    client: TestClient, manager: ConfigManager, lister: FakeLister
):
    seed(manager, openai={"base_url": OLLAMA})

    verify(client, "openai", {"api_key": "sk-typed"})

    assert lister.calls == [("openai", "sk-typed", OLLAMA)]


def test_a_blank_endpoint_is_the_providers_own(
    client: TestClient, manager: ConfigManager, lister: FakeLister
):
    seed(manager, openai={"base_url": OLLAMA})

    verify(client, "openai", {"api_key": "sk-typed", "base_url": ""})

    assert lister.calls == [("openai", "sk-typed", None)]


@pytest.mark.parametrize(
    "saved_endpoint, requested",
    [(None, ATTACKER), (OLLAMA, ATTACKER), (OLLAMA, "")],
)
def test_verify_never_sends_the_saved_key_to_another_endpoint(
    client: TestClient,
    manager: ConfigManager,
    lister: FakeLister,
    saved_endpoint,
    requested: str,
):
    seed(manager, openai={"api_key": KEY, "base_url": saved_endpoint})

    response = verify(client, "openai", {"base_url": requested})

    assert response.status_code == 422
    assert lister.calls == []


@pytest.mark.parametrize("requested", [None, f"  {OLLAMA}/ "])
def test_verify_checks_the_saved_key_at_the_saved_endpoint(
    client: TestClient, manager: ConfigManager, lister: FakeLister, requested
):
    """The same endpoint typed again is not a change."""
    seed(manager, openai={"api_key": "ollama", "base_url": OLLAMA})
    body = {} if requested is None else {"base_url": requested}

    response = verify(client, "openai", body)

    assert response.status_code == 200
    assert lister.calls == [("openai", "ollama", OLLAMA)]


def test_verify_with_a_key_that_could_not_be_read(
    client: TestClient, lister: FakeLister, unreadable_key: str
):
    response = verify(client, "openai", {})

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["key_state"] == "unreadable"
    assert lister.calls == []


def test_verify_without_any_key(client: TestClient, lister: FakeLister):
    response = verify(client, "gemini", {})

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["key_state"] == "unverified"
    assert body["message"]
    assert lister.calls == []


def test_gemini_ignores_an_endpoint(
    client: TestClient, manager: ConfigManager, lister: FakeLister
):
    """Gemini takes no endpoint, so naming one moves nothing — and the saved
    key still goes only to Google."""
    seed(manager, gemini={"api_key": KEY})

    response = verify(client, "gemini", {"base_url": ATTACKER})

    assert response.status_code == 200
    assert lister.calls == [("gemini", KEY, None)]


def test_argos_has_no_key_to_verify(client: TestClient, lister: FakeLister):
    assert verify(client, "argos", {}).status_code == 422
    assert lister.calls == []


def test_a_key_that_lists_is_valid_and_returns_its_models(client: TestClient):
    body = verify(client, "openai", {"api_key": "sk-typed"}).json()

    assert body["valid"] is True
    assert body["key_state"] == "valid"
    assert body["model_found"] is None
    assert ids_and_sources(body["models"]) == listed(DEFAULT_LISTING.models)
    assert ids_and_sources(body["hidden"]) == listed(DEFAULT_LISTING.hidden)


@pytest.mark.parametrize(
    "model, found",
    [
        ("claude-sonnet-4-6", True),
        # Anthropic's alias for the dated snapshot it lists.
        ("claude-haiku-4-5", True),
        # The filter hid it, but the endpoint did name it.
        ("claude-embed-v1", True),
        ("claude-sonet-4-6", False),
        # A prefix that stops mid-word is a different name, not an alias.
        ("claude-haik", False),
    ],
)
def test_verify_says_whether_the_model_is_listed(
    client: TestClient, lister: FakeLister, model: str, found: bool
):
    lister.result = ANTHROPIC_LISTING

    body = verify(client, "anthropic", {"api_key": "sk-typed", "model": model}).json()

    assert body["model_found"] is found
    # A model the key can't reach is not a key that doesn't work.
    assert body["valid"] is True
    assert body["key_state"] == "valid"


def test_a_refused_key_does_not_verify(client: TestClient, lister: FakeLister):
    lister.result = catalog.KeyRejected("401 Unauthorized")

    body = verify(client, "openai", {"api_key": "sk-typo"}).json()

    assert body["valid"] is False
    assert body["key_state"] == "invalid"


def test_a_listing_failure_says_why(client: TestClient, lister: FakeLister):
    lister.result = catalog.ListingFailed("Connection refused")

    body = verify(client, "openai", {"api_key": "ollama", "base_url": OLLAMA}).json()

    assert body["valid"] is False
    assert body["key_state"] == "unverified"
    assert "Connection refused" in body["message"]


def test_verifying_the_saved_pair_stores_the_listing(
    client: TestClient, manager: ConfigManager, store: ModelCatalog
):
    seed(manager, openai={"api_key": "ollama", "base_url": OLLAMA})

    verify(client, "openai", {})

    entry = store.get("openai", OLLAMA)
    assert entry is not None
    assert entry.key_state == "valid"
    assert entry.listing == DEFAULT_LISTING


def test_a_refusal_of_the_saved_pair_is_recorded(
    client: TestClient, manager: ConfigManager, store: ModelCatalog, lister: FakeLister
):
    seed(manager, anthropic={"api_key": KEY})
    store.put("anthropic", None, ANTHROPIC_LISTING)
    lister.result = catalog.KeyRejected("401 Unauthorized")

    verify(client, "anthropic", {})

    entry = store.get("anthropic", None)
    assert entry is not None and entry.key_state == "invalid"


@pytest.mark.parametrize(
    "body",
    [
        # A key the user is trying out says nothing about the saved one.
        {"api_key": "sk-other"},
        # Nor does another endpoint.
        {"api_key": KEY, "base_url": OLLAMA},
    ],
)
@pytest.mark.parametrize(
    "result",
    [DEFAULT_LISTING, catalog.KeyRejected("401 Unauthorized")],
    ids=["listed", "refused"],
)
def test_verifying_anything_else_leaves_the_catalog_alone(
    client: TestClient,
    manager: ConfigManager,
    store: ModelCatalog,
    lister: FakeLister,
    body: dict,
    result,
):
    seed(manager, openai={"api_key": KEY})
    at = fresh()
    store.put("openai", None, OTHER_LISTING, now=at)
    lister.result = result

    assert verify(client, "openai", body).status_code == 200

    assert lister.calls  # it was checked, just not recorded
    entry = store.get("openai", None)
    assert entry is not None
    assert (entry.key_state, entry.listing, entry.fetched_at) == ("valid", OTHER_LISTING, at)
    assert store.get("openai", OLLAMA) is None


# ---------------------------------------------------------------------------
# PUT /config's promotion, seen from here
# ---------------------------------------------------------------------------


def test_the_listing_a_promotion_made_shows_the_key_as_valid(
    client: TestClient, lister: FakeLister
):
    """The key was just listed; asking again on the next screen would be a
    second round-trip for nothing."""
    response = client.put("/config", json={"openai": {"api_key": KEY}}, headers=AUTH)

    assert response.status_code == 200
    assert lister.calls == [("openai", KEY, None)]
    assert providers(client)["openai"]["key_state"] == "valid"


# ---------------------------------------------------------------------------
# never generates (#84's acceptance criterion)
# ---------------------------------------------------------------------------


def test_no_flow_that_checks_a_key_generates_text(
    client: TestClient,
    manager: ConfigManager,
    lister: FakeLister,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify, Save (`/config/validate`), the Argos → LLM promotion and the
    model pickers each check a key by listing, and none of them builds a
    translator or asks one to validate — which sent a "Hello" completion."""
    guard = install_full_guard(monkeypatch)
    lister.result = ANTHROPIC_LISTING

    # Auto-promotion: first key saved while still on Argos.
    assert client.put("/config", json={"anthropic": {"api_key": KEY}}, headers=AUTH).status_code == 200
    # Save's check.
    assert client.post(
        "/config/validate", json={"service": "anthropic"}, headers=AUTH
    ).status_code == 200
    assert guard.calls == []
    # Verify.
    assert verify(client, "anthropic", {"model": "claude-sonnet-4-6"}).status_code == 200
    # The model pickers.
    assert models(client, "anthropic", refresh=True).status_code == 200
    assert client.get("/config/models/anthropic", headers=AUTH).status_code == 200

    assert guard.calls == []
    # Each of the first four listed (the last may be served from the catalog).
    assert len(lister.calls) >= 4
    assert all(call == ("anthropic", KEY, None) for call in lister.calls)
