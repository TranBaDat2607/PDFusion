"""`/config`: model names, endpoints, and the key that goes with an endpoint (#32).

Only the config router is mounted, so no lifespan runs, and settings live in a
`ConfigManager` under `tmp_path`. Nothing here reaches a provider: a key is
checked by listing the models it can use (#84), through the one function
`providers.catalog.list_models`, which is replaced by a recorder
(`provider_fakes.FakeLister`) noting which key went to which endpoint. The
catalog that keeps what a listing said lives under `tmp_path` too.
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from desktop_pdf_translator.api import auth
from desktop_pdf_translator.api.routes import config as config_routes
from desktop_pdf_translator.api.routes import providers as providers_routes
from desktop_pdf_translator.config import AppSettings
from desktop_pdf_translator.config.manager import ConfigManager
from desktop_pdf_translator.processors.pdf_cache import PDFTranslationCache
from desktop_pdf_translator.providers import catalog
from desktop_pdf_translator.providers.catalog import ModelCatalog
from desktop_pdf_translator.providers.listing import ListedModel, Listing
from desktop_pdf_translator.translators.translation_cache import TranslationCache
from desktop_pdf_translator.utils.encryption import KEYSTORE_PREFIX

from conftest import MINIMAL_PDF
from provider_fakes import (
    FakeLister,
    GenerationGuard,
    install_catalog,
    install_factory_guard,
    install_lister,
    seed,
)

TOKEN = "test-token-for-config-api"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
KEY = "sk-test-0123456789abcdef"
OLLAMA = "http://localhost:11434/v1"


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "_TOKEN", TOKEN)


@pytest.fixture
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ConfigManager:
    """A manager over a throwaway dir, with the developer's own credentials out
    of the way, as `test_config_manager_load.py` does."""
    monkeypatch.setattr(ConfigManager, "_load_dotenv", lambda self: None)
    for service in ("OPENAI", "GEMINI", "ANTHROPIC"):
        monkeypatch.delenv(f"{service}_API_KEY", raising=False)
        monkeypatch.delenv(f"{service}_MODEL", raising=False)
    manager = ConfigManager(config_dir=tmp_path / "PDFusion")
    monkeypatch.setattr(config_routes, "get_config_manager", lambda: manager)
    monkeypatch.setattr(config_routes, "get_settings", lambda: manager.settings)
    # `/config/validate` and `/config/models` wrap the providers route, which
    # reads the settings itself.
    monkeypatch.setattr(providers_routes, "get_config_manager", lambda: manager)
    monkeypatch.setattr(providers_routes, "get_settings", lambda: manager.settings)
    return manager


@pytest.fixture
def lister(monkeypatch: pytest.MonkeyPatch) -> FakeLister:
    """Stands in for `catalog.list_models`, recording which key was listed at
    which endpoint."""
    return install_lister(monkeypatch)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModelCatalog:
    return install_catalog(monkeypatch, tmp_path)


@pytest.fixture
def factory_guard(monkeypatch: pytest.MonkeyPatch) -> GenerationGuard:
    """No translator is ever built to check a key (#84) — and a route that
    still tries stays off the network."""
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
    return TestClient(app)


def put(client: TestClient, body: dict):
    return client.put("/config", json=body, headers=AUTH)


def validate(client: TestClient, body: dict):
    return client.post("/config/validate", json=body, headers=AUTH)


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------


def test_an_endpoint_is_saved_with_its_key(client: TestClient, manager: ConfigManager):
    response = put(client, {"openai": {"api_key": "ollama", "base_url": f"{OLLAMA}/"}})

    assert response.status_code == 200
    assert response.json()["openai"]["base_url"] == OLLAMA
    reloaded = ConfigManager(config_dir=manager.config_dir).load_settings()
    assert reloaded.providers["openai"].base_url == OLLAMA
    assert reloaded.providers["openai"].api_key == "ollama"


def test_the_promotion_listing_checks_the_new_endpoint(
    client: TestClient, lister: FakeLister
):
    put(client, {"openai": {"api_key": "ollama", "base_url": OLLAMA}})

    assert lister.calls == [("openai", "ollama", OLLAMA)]


def test_changing_the_endpoint_needs_the_key_again(
    client: TestClient, manager: ConfigManager
):
    """Otherwise anything holding the bearer token could send the saved key to
    a server of its own, while `GET /config` never reveals it."""
    assert put(client, {"openai": {"api_key": KEY}}).status_code == 200
    before = manager.config_file.read_text(encoding="utf-8")

    response = put(client, {"openai": {"base_url": "https://attacker.example/v1"}})

    assert response.status_code == 422
    assert "API key" in response.json()["detail"]
    assert manager.config_file.read_text(encoding="utf-8") == before
    assert manager.settings.providers["openai"].base_url is None


@pytest.mark.parametrize("typed_key, saved_key", [("ollama", "ollama"), ("", None)])
def test_a_key_from_the_environment_never_reaches_a_new_endpoint(
    client: TestClient,
    manager: ConfigManager,
    monkeypatch: pytest.MonkeyPatch,
    typed_key: str,
    saved_key: str | None,
):
    """The request carries a key, so the change is allowed. The key from the
    environment replaces the saved one on every start, and would then be sent
    to the new endpoint."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    attacker = "https://attacker.example/v1"

    response = put(client, {"openai": {"api_key": typed_key, "base_url": attacker}})

    assert response.status_code == 200
    reloaded = ConfigManager(config_dir=manager.config_dir).load_settings()
    assert reloaded.providers["openai"].base_url == attacker
    assert (reloaded.providers["openai"].api_key or None) == saved_key


def test_returning_to_the_provider_endpoint_needs_the_key_too(
    client: TestClient, manager: ConfigManager
):
    put(client, {"anthropic": {"api_key": "ollama", "base_url": "http://localhost:11434"}})

    assert put(client, {"anthropic": {"base_url": ""}}).status_code == 422
    assert manager.settings.providers["anthropic"].base_url == "http://localhost:11434"


def test_the_same_endpoint_typed_again_is_not_a_change(client: TestClient):
    put(client, {"openai": {"api_key": "ollama", "base_url": OLLAMA}})

    assert put(client, {"openai": {"base_url": f"  {OLLAMA}/ "}}).status_code == 200


def test_an_endpoint_needs_no_key_when_none_is_saved(
    client: TestClient, manager: ConfigManager
):
    assert put(client, {"openai": {"base_url": OLLAMA}}).status_code == 200
    assert manager.settings.providers["openai"].base_url == OLLAMA


# ---------------------------------------------------------------------------
# a saved key this process cannot read
# ---------------------------------------------------------------------------


@pytest.fixture
def unreadable_key(manager: ConfigManager) -> str:
    """A key that is stored but undecryptable here: a `keystore:` value with no
    keystore to open it.

    Which is the state a locked keyring, a dismissed unlock prompt or a config
    carried between machines leaves behind. conftest's autouse
    `_no_real_keystore` guarantees the "no keystore" half on every platform, so
    this needs no marker.
    """
    stored = KEYSTORE_PREFIX + "c3RvcmVkLWNpcGhlcnRleHQ="
    manager.config_file.write_text(
        f'[openai]\napi_key = "{stored}"\n', encoding="utf-8"
    )
    manager._settings = None
    assert manager.settings.providers["openai"].api_key is None
    assert manager.has_unreadable_key("openai")
    return stored


def test_an_unrelated_save_keeps_a_key_it_could_not_read(
    client: TestClient, manager: ConfigManager, unreadable_key: str
):
    """Any `PUT /config` used to blank it — the key came back empty, and the
    save path could not tell that from the user clearing it."""
    assert put(client, {"chat_enabled": True}).status_code == 200

    assert unreadable_key in manager.config_file.read_text(encoding="utf-8")


def test_changing_the_endpoint_needs_a_key_that_could_not_be_read_again(
    client: TestClient, manager: ConfigManager, unreadable_key: str
):
    """Preserving the ciphertext brings the #32 rule back into play: the key
    decrypts again once the keystore is reachable, and would then go to
    whatever endpoint was set meanwhile. `GET /config` reports no key for this
    service, so the sheet does not pre-empt this one — the 422 names the fix."""
    response = put(client, {"openai": {"base_url": "https://attacker.example/v1"}})

    assert response.status_code == 422
    assert "API key" in response.json()["detail"]
    assert manager.settings.providers["openai"].base_url is None
    assert unreadable_key in manager.config_file.read_text(encoding="utf-8")


def test_a_key_typed_over_one_that_could_not_be_read_replaces_it(
    client: TestClient, manager: ConfigManager, unreadable_key: str
):
    response = put(client, {"openai": {"api_key": KEY, "base_url": OLLAMA}})

    assert response.status_code == 200
    assert unreadable_key not in manager.config_file.read_text(encoding="utf-8")
    assert not manager.has_unreadable_key("openai")
    reloaded = ConfigManager(config_dir=manager.config_dir).load_settings()
    assert reloaded.providers["openai"].api_key == KEY
    assert reloaded.providers["openai"].base_url == OLLAMA


def test_clearing_a_key_that_could_not_be_read_clears_it(
    client: TestClient, manager: ConfigManager, unreadable_key: str
):
    assert put(client, {"openai": {"api_key": ""}}).status_code == 200

    assert unreadable_key not in manager.config_file.read_text(encoding="utf-8")
    assert not manager.has_unreadable_key("openai")


def test_a_refused_endpoint_change_forgets_no_preserved_key(
    client: TestClient, manager: ConfigManager, unreadable_key: str
):
    """Why the endpoint checks all happen before anything is applied.

    Services are walked in order, openai first. A body that types an openai
    key and moves anthropic's endpoint would drop openai's preserved
    ciphertext on its way to anthropic's 422 — nothing saved, the record gone,
    and the next unrelated save blanking a key the file still held.
    """
    assert put(client, {"anthropic": {"api_key": "sk-anthropic"}}).status_code == 200

    refused = put(
        client,
        {
            "openai": {"api_key": "sk-typed"},
            "anthropic": {"base_url": "https://attacker.example"},
        },
    )

    assert refused.status_code == 422
    assert manager.has_unreadable_key("openai")
    assert manager.settings.providers["openai"].api_key is None  # nothing was applied
    assert put(client, {"chat_enabled": True}).status_code == 200
    assert unreadable_key in manager.config_file.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "base_url", ["localhost:11434", "ftp://example.com", "http://", "/"]
)
def test_an_endpoint_must_be_a_web_url(client: TestClient, base_url: str):
    assert put(client, {"openai": {"base_url": base_url}}).status_code == 422


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


def test_a_model_is_any_name_trimmed(client: TestClient, manager: ConfigManager):
    assert put(client, {"openai": {"model": "  llama3.2:3b "}}).status_code == 200
    assert manager.settings.model_for("openai") == "llama3.2:3b"


def test_a_blank_model_is_refused(client: TestClient):
    assert put(client, {"gemini": {"model": "   "}}).status_code == 422


def test_every_default_model_is_the_first_suggestion(client: TestClient):
    """A default missing from the suggestions is how `gemini-1.5-flash` stayed
    on offer after Google retired it."""
    services = client.get("/config/options", headers=AUTH).json()["services"]
    defaults = AppSettings()

    for service in services:
        assert service["models"][0] == defaults.model_for(service["code"])


# ---------------------------------------------------------------------------
# endpoint models
# ---------------------------------------------------------------------------

LOCAL_LISTING = Listing(
    models=(
        ListedModel(id="qwen2.5:7b"),
        ListedModel(id="llama3.2:3b"),
        ListedModel(id="qwen2.5:7b"),
    ),
    hidden=(ListedModel(id="nomic-embed-text"),),
)


def endpoint_models(client: TestClient, service: str):
    return client.get(f"/config/models/{service}", headers=AUTH)


def test_endpoint_models_use_the_saved_key_and_endpoint(
    client: TestClient, manager: ConfigManager, lister: FakeLister
):
    """Only what the endpoint listed as a chat model: not what the filter hid,
    and not the saved model the list lacks."""
    seed(manager, openai={"api_key": "ollama", "base_url": OLLAMA})
    lister.result = LOCAL_LISTING

    response = endpoint_models(client, "openai")

    assert response.status_code == 200
    assert response.json() == {"models": ["llama3.2:3b", "qwen2.5:7b"], "error": None}
    assert lister.calls == [("openai", "ollama", OLLAMA)]


def test_endpoint_models_need_a_saved_key(client: TestClient, lister: FakeLister):
    response = endpoint_models(client, "anthropic")

    assert response.status_code == 200
    assert response.json()["models"] == []
    assert response.json()["error"]
    assert lister.calls == []


def test_a_server_that_is_down_is_an_error_not_a_failure(
    client: TestClient, manager: ConfigManager, lister: FakeLister
):
    seed(manager, openai={"api_key": "ollama", "base_url": OLLAMA})
    lister.result = catalog.ListingFailed("Connection refused")

    response = endpoint_models(client, "openai")

    assert response.status_code == 200
    assert response.json() == {"models": [], "error": "Connection refused"}


def test_argos_has_no_model_list(client: TestClient, lister: FakeLister):
    assert endpoint_models(client, "argos").status_code == 422
    assert lister.calls == []


def test_gemini_lists_its_models_too(
    client: TestClient, manager: ConfigManager, lister: FakeLister
):
    """It used to be refused: only a custom endpoint was listed. Every keyed
    provider now has a list endpoint (#84)."""
    seed(manager, gemini={"api_key": KEY})
    lister.result = Listing(
        models=(ListedModel(id="gemini-3.8-flash"), ListedModel(id="gemini-3.5-flash"))
    )

    response = endpoint_models(client, "gemini")

    assert response.status_code == 200
    assert response.json() == {
        "models": ["gemini-3.5-flash", "gemini-3.8-flash"],
        "error": None,
    }
    assert lister.calls == [("gemini", KEY, None)]


def test_endpoint_models_use_a_fresh_catalog_entry(
    client: TestClient, manager: ConfigManager, store: ModelCatalog, lister: FakeLister
):
    seed(manager, openai={"api_key": "ollama", "base_url": OLLAMA})
    store.put("openai", OLLAMA, LOCAL_LISTING)

    response = endpoint_models(client, "openai")

    assert response.json() == {"models": ["llama3.2:3b", "qwen2.5:7b"], "error": None}
    assert lister.calls == []


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_checks_the_saved_key_against_the_saved_endpoint(
    client: TestClient, lister: FakeLister
):
    """`llama3.2` is Ollama's alias for the `llama3.2:3b` it lists."""
    put(
        client,
        {"openai": {"api_key": "ollama", "base_url": OLLAMA, "model": "llama3.2"}},
    )
    lister.calls.clear()

    response = validate(client, {"service": "openai"})

    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert lister.calls == [("openai", "ollama", OLLAMA)]


def test_validate_never_sends_the_saved_key_to_another_endpoint(
    client: TestClient, lister: FakeLister
):
    put(client, {"openai": {"api_key": KEY}})
    lister.calls.clear()

    response = validate(
        client, {"service": "openai", "base_url": "https://attacker.example/v1"}
    )

    assert response.status_code == 422
    assert lister.calls == []


def test_validate_checks_any_endpoint_with_a_typed_key(
    client: TestClient, lister: FakeLister
):
    response = validate(
        client,
        {
            "service": "anthropic",
            "api_key": "ollama",
            "base_url": "http://localhost:11434",
            "model": "qwen3",
        },
    )

    assert response.status_code == 200
    # `qwen3` is listed, and the saved `claude-sonnet-4-6` is not: only the
    # typed model can make this valid.
    assert response.json()["valid"] is True
    assert lister.calls == [("anthropic", "ollama", "http://localhost:11434")]


def test_validate_without_any_key_does_not_probe(client: TestClient, lister: FakeLister):
    response = validate(client, {"service": "gemini"})

    assert response.json()["valid"] is False
    assert lister.calls == []


def test_validate_fails_a_model_the_endpoint_does_not_list(client: TestClient):
    """The key works, but Save would store a model every paragraph then fails
    on — so it is not valid, and the message names the model."""
    response = validate(
        client, {"service": "openai", "api_key": KEY, "model": "gpt-4.2-typo"}
    )

    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert "gpt-4.2-typo" in response.json()["message"]


def test_validate_accepts_an_alias_of_a_listed_model(
    client: TestClient, lister: FakeLister
):
    lister.result = Listing(models=(ListedModel(id="claude-haiku-4-5-20251001"),))

    response = validate(
        client, {"service": "anthropic", "api_key": KEY, "model": "claude-haiku-4-5"}
    )

    assert response.json()["valid"] is True


def test_validate_checks_the_saved_model_when_none_is_given(
    client: TestClient, manager: ConfigManager, lister: FakeLister
):
    seed(manager, openai={"api_key": KEY, "model": "my-finetune"})

    response = validate(client, {"service": "openai"})

    assert response.json()["valid"] is False
    assert "my-finetune" in response.json()["message"]
    assert lister.calls == [("openai", KEY, None)]


@pytest.mark.parametrize(
    "failure",
    [catalog.KeyRejected("401 Unauthorized"), catalog.ListingFailed("Connection refused")],
    ids=["refused", "down"],
)
def test_validate_fails_when_nothing_is_listed(
    client: TestClient, lister: FakeLister, failure: Exception
):
    lister.result = failure

    response = validate(client, {"service": "openai", "api_key": KEY})

    assert response.status_code == 200
    assert response.json()["valid"] is False


# ---------------------------------------------------------------------------
# promotion off Argos
# ---------------------------------------------------------------------------


def preferred(response) -> str:
    return response.json()["translation"]["preferred_service"]


def test_a_first_key_whose_model_is_listed_promotes_off_argos(
    client: TestClient, lister: FakeLister
):
    response = put(client, {"openai": {"api_key": KEY}})

    assert response.status_code == 200
    assert preferred(response) == "openai"
    assert lister.calls == [("openai", KEY, None)]


def test_promotion_accepts_an_alias_of_a_listed_model(
    client: TestClient, lister: FakeLister
):
    lister.result = Listing(models=(ListedModel(id="claude-haiku-4-5-20251001"),))

    response = put(client, {"anthropic": {"api_key": KEY, "model": "claude-haiku-4-5"}})

    assert preferred(response) == "anthropic"


def test_promotion_lists_only_the_highest_priority_new_key(
    client: TestClient, lister: FakeLister
):
    response = put(
        client,
        {
            "gemini": {"api_key": "sk-gemini"},
            "anthropic": {"api_key": "sk-anthropic"},
            "openai": {"api_key": "sk-openai"},
        },
    )

    assert preferred(response) == "openai"
    assert lister.calls == [("openai", "sk-openai", None)]


@pytest.mark.parametrize(
    "result",
    [
        catalog.KeyRejected("401 Unauthorized"),
        catalog.ListingFailed("Connection refused"),
        # The key works, but not with the saved `gpt-4.1`.
        Listing(models=(ListedModel(id="gpt-4o"), ListedModel(id="o3"))),
    ],
    ids=["refused", "down", "model-not-listed"],
)
def test_no_promotion_without_a_listing_that_has_the_model(
    client: TestClient, manager: ConfigManager, lister: FakeLister, result
):
    """Moving the user off Argos onto a translator that fails every paragraph
    is worse than staying — but the key is still saved."""
    lister.result = result

    response = put(client, {"openai": {"api_key": KEY}})

    assert response.status_code == 200
    assert preferred(response) == "argos"
    assert lister.calls == [("openai", KEY, None)]
    reloaded = ConfigManager(config_dir=manager.config_dir).load_settings()
    assert reloaded.providers["openai"].api_key == KEY


def test_a_key_the_promotion_saw_refused_is_recorded_invalid(
    client: TestClient, store: ModelCatalog, lister: FakeLister
):
    """`/providers` would otherwise call a key the provider just turned down
    `unverified`."""
    lister.result = catalog.KeyRejected("Incorrect API key provided")

    assert put(client, {"openai": {"api_key": KEY}}).status_code == 200

    assert store.get("openai", None).key_state == "invalid"


@pytest.mark.parametrize("endpoint", [None, OLLAMA])
def test_the_promotion_listing_is_kept_for_the_saved_endpoint(
    client: TestClient, store: ModelCatalog, endpoint
):
    body = {"api_key": KEY} if endpoint is None else {"api_key": KEY, "base_url": endpoint}

    put(client, {"openai": body})

    entry = store.get("openai", endpoint)
    assert entry is not None
    assert entry.key_state == "valid"
    assert entry.listing is not None
    assert [model.id for model in entry.listing.models] == ["gpt-4.1", "llama3.2:3b", "qwen3"]


def test_a_listing_without_the_model_is_kept_too(
    client: TestClient, store: ModelCatalog, lister: FakeLister
):
    """It did not promote, but it did list: the key is known to work."""
    lister.result = Listing(models=(ListedModel(id="gpt-4o"),))

    put(client, {"openai": {"api_key": KEY}})

    entry = store.get("openai", None)
    assert entry is not None and entry.key_state == "valid"


# ---------------------------------------------------------------------------
# the catalog after a save
# ---------------------------------------------------------------------------

SOME_LISTING = Listing(models=(ListedModel(id="gpt-4.1"),))


def fill(store: ModelCatalog) -> None:
    for provider_id, endpoint in (("openai", None), ("openai", OLLAMA), ("gemini", None)):
        store.put(provider_id, endpoint, SOME_LISTING)


@pytest.mark.parametrize(
    "saved, body",
    [
        ({}, {"openai": {"api_key": "sk-new"}}),
        ({"api_key": KEY}, {"openai": {"api_key": ""}}),
        ({}, {"openai": {"base_url": OLLAMA}}),
    ],
    ids=["key-set", "key-cleared", "endpoint-changed"],
)
def test_a_save_that_changes_a_key_or_endpoint_drops_its_catalog_rows(
    client: TestClient, manager: ConfigManager, store: ModelCatalog, saved: dict, body: dict
):
    """What was listed was listed for the old key or at the old endpoint. Not
    on Argos, so no promotion lists anything back in."""
    seed(manager, preferred_service="openai", openai=saved)
    fill(store)

    assert put(client, body).status_code == 200

    assert store.get("openai", None) is None
    assert store.get("openai", OLLAMA) is None
    assert store.get("gemini", None) is not None


@pytest.mark.parametrize(
    "body",
    [
        {"openai": {"model": "gpt-5.6-luna"}},
        {"chat_enabled": False},
        # Typed again, the same endpoint is not a change.
        {"openai": {"base_url": f"  {OLLAMA}/ "}},
        # Another provider's key is that provider's business.
        {"anthropic": {"api_key": "sk-anthropic"}},
    ],
)
def test_a_save_that_changes_neither_keeps_the_catalog(
    client: TestClient, manager: ConfigManager, store: ModelCatalog, body: dict
):
    seed(manager, preferred_service="openai", openai={"api_key": "ollama", "base_url": OLLAMA})
    fill(store)

    assert put(client, body).status_code == 200

    assert store.get("openai", None) is not None
    assert store.get("openai", OLLAMA) is not None
    assert store.get("gemini", None) is not None


def test_a_refused_save_keeps_the_catalog(
    client: TestClient, manager: ConfigManager, store: ModelCatalog
):
    seed(manager, preferred_service="openai", openai={"api_key": KEY})
    fill(store)

    assert put(client, {"openai": {"base_url": "https://attacker.example/v1"}}).status_code == 422

    assert store.get("openai", None) is not None


def test_a_save_that_fails_keeps_the_catalog(
    client: TestClient,
    manager: ConfigManager,
    store: ModelCatalog,
    monkeypatch: pytest.MonkeyPatch,
):
    """The file still holds the old key, so its rows still describe it: the
    catalog is only dropped once the new key is on disk."""
    seed(manager, preferred_service="openai", openai={"api_key": KEY})
    fill(store)
    monkeypatch.setattr(manager, "save_settings", lambda settings: False)

    assert put(client, {"openai": {"api_key": "sk-new"}}).status_code == 500

    assert store.get("openai", None) is not None


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------


def test_chat_is_on_until_it_is_turned_off(client: TestClient, manager: ConfigManager):
    assert client.get("/config", headers=AUTH).json()["rag"]["chat_enabled"] is True

    response = put(client, {"chat_enabled": False})

    assert response.json()["rag"]["chat_enabled"] is False
    reloaded = ConfigManager(config_dir=manager.config_dir).load_settings()
    assert reloaded.rag.chat_enabled is False


# ---------------------------------------------------------------------------
# translation limits (#33)
# ---------------------------------------------------------------------------


def test_the_translation_limits_are_saved(client: TestClient, manager: ConfigManager):
    response = put(client, {"max_pages": 75, "max_file_size_mb": 120})

    assert response.status_code == 200
    translation = response.json()["translation"]
    assert (translation["max_pages"], translation["max_file_size_mb"]) == (75, 120.0)
    reloaded = ConfigManager(config_dir=manager.config_dir).load_settings()
    assert reloaded.translation.max_pages == 75
    assert reloaded.translation.max_file_size_mb == 120.0


@pytest.mark.parametrize(
    "body",
    [
        {"max_pages": 0},
        {"max_pages": 101},
        {"max_file_size_mb": 0.5},
        {"max_file_size_mb": 201},
    ],
)
def test_a_limit_outside_its_bounds_is_refused(
    client: TestClient, manager: ConfigManager, body: dict
):
    """422 from the request, not 500 from the settings model: both read the
    same bounds (`config/models.py:MaxPages`, `MaxFileSizeMB`)."""
    assert put(client, body).status_code == 422
    reloaded = ConfigManager(config_dir=manager.config_dir).load_settings()
    assert reloaded.translation.max_pages == 50


# ---------------------------------------------------------------------------
# caches
# ---------------------------------------------------------------------------


@pytest.fixture
def caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Tuple[TranslationCache, PDFTranslationCache]:
    """One entry in each cache, both under `tmp_path` rather than the
    singletons, with the PDF cache's settings lookup stubbed out."""
    monkeypatch.setattr(
        PDFTranslationCache, "_refresh_cap_from_settings", lambda self: None
    )
    paragraph = TranslationCache(cache_dir=tmp_path / "translation_cache")
    pdf = PDFTranslationCache(cache_dir=tmp_path / "translated_pdf_cache")
    monkeypatch.setattr(config_routes, "get_translation_cache", lambda: paragraph)
    monkeypatch.setattr(config_routes, "get_pdf_cache", lambda: pdf)

    paragraph.set(
        "Hello", "Xin chào", lang_in="en", lang_out="vi", service="openai", model="gpt-4.1"
    )
    source = tmp_path / "paper.pdf"
    source.write_bytes(MINIMAL_PDF)
    translated = tmp_path / "job" / "paper_translated_v001.pdf"
    translated.parent.mkdir()
    translated.write_bytes(MINIMAL_PDF + b"% translated\n")
    assert pdf.store(
        source, translated, source_lang="en", target_lang="vi", service="openai", model="gpt-4.1"
    )
    return paragraph, pdf


def test_the_cache_stats_cover_both_caches(client: TestClient, caches):
    body = client.get("/config/cache", headers=AUTH).json()

    assert body["paragraph"]["entries"] == 1
    assert body["pdf"]["entries"] == 1
    assert body["pdf"]["max_size_mb"] == 1000.0


@pytest.mark.parametrize(
    "target, left",
    [("paragraph", (0, 1)), ("pdf", (1, 0)), ("all", (0, 0))],
)
def test_clearing_one_cache_leaves_the_other(
    client: TestClient, caches, target: str, left: Tuple[int, int]
):
    """The tab's "Clear all" used to empty the PDF cache too, while showing
    only the paragraph cache."""
    paragraph, pdf = caches

    response = client.delete(f"/config/cache?scope=all&target={target}", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["target"] == target
    assert (paragraph.stats()["entries"], pdf.stats()["entries"]) == left


def test_removing_expired_entries_never_touches_the_pdf_cache(
    client: TestClient, caches
):
    _, pdf = caches

    client.delete("/config/cache?scope=expired&target=all", headers=AUTH)

    assert pdf.stats()["entries"] == 1


@pytest.mark.parametrize("query", ["target=everything", "scope=some"])
def test_an_unknown_cache_or_scope_is_refused(client: TestClient, caches, query: str):
    """Both used to fall through to clearing everything."""
    paragraph, pdf = caches

    assert client.delete(f"/config/cache?{query}", headers=AUTH).status_code == 422
    assert (paragraph.stats()["entries"], pdf.stats()["entries"]) == (1, 1)
