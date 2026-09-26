"""`PUT /providers/{id}`, `DELETE /providers/{id}/key`, and `/config` on v2 (#85).

Settings are per provider now (`[providers.<id>]`), and a model is chosen as
`{provider, model}`. `PUT /providers/{id}` edits one provider's table;
`PUT /config` takes `translation_model` and `answer_model`. The per-service
blocks and `preferred_service` it accepted as a compatibility layer were
retired in #88, and their key rules ported here.

The key rules carry over unchanged from `/config` (#32, #84): a saved key only
ever goes to the endpoint it was saved for, a key this process cannot decrypt
counts as saved and is never blanked, and a key or endpoint change drops that
provider's rows from the model catalog.

Both routers are mounted over a `ConfigManager` under `tmp_path`, the catalog
lives under `tmp_path`, and `catalog.list_models` is a recorder — nothing
reaches a provider.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomlkit
from fastapi import FastAPI
from fastapi.testclient import TestClient

from desktop_pdf_translator.api import auth
from desktop_pdf_translator.api.routes import config as config_routes
from desktop_pdf_translator.api.routes import providers as providers_routes
from desktop_pdf_translator.config import models as config_models
from desktop_pdf_translator.config.manager import ConfigManager
from desktop_pdf_translator.providers.catalog import ModelCatalog
from desktop_pdf_translator.providers.listing import ListedModel, Listing
from desktop_pdf_translator.utils.encryption import KEYSTORE_PREFIX

from provider_fakes import (
    ATTACKER,
    OLLAMA,
    FakeLister,
    GenerationGuard,
    install_catalog,
    install_factory_guard,
    install_lister,
)

TOKEN = "test-token-for-provider-config-api"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
KEY = "sk-test-0123456789abcdef"
SOME_LISTING = Listing(models=(ListedModel(id="gpt-4.1"),))


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "_TOKEN", TOKEN)


@pytest.fixture
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ConfigManager:
    """A manager over a throwaway dir, with the developer's own credentials out
    of the way; both route modules read settings through it."""
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


def seed(manager: ConfigManager, **fields) -> None:
    """Save v2 settings straight through the manager, as a previous session
    would have left them — without a route, whose save would invalidate the
    catalog and whose promotion would list."""
    settings = config_models.AppSettings(**fields)
    assert manager.save_settings(settings)
    manager._settings = settings


@pytest.fixture
def unreadable_key(manager: ConfigManager) -> str:
    """A stored openai key this process cannot decrypt: a `keystore:` value
    with no keystore to open it (conftest's `_no_real_keystore`)."""
    stored = KEYSTORE_PREFIX + "c3RvcmVkLWNpcGhlcnRleHQ="
    manager.config_file.write_text(
        f'[providers.openai]\napi_key = "{stored}"\n', encoding="utf-8"
    )
    manager._settings = None
    manager.settings  # the load is what records a key as unreadable
    assert manager.has_unreadable_key("openai")
    return stored


def reloaded(manager: ConfigManager):
    return ConfigManager(config_dir=manager.config_dir).load_settings()


def put_provider(client: TestClient, provider_id: str, body: dict):
    return client.put(f"/providers/{provider_id}", json=body, headers=AUTH)


def delete_key(client: TestClient, provider_id: str):
    return client.delete(f"/providers/{provider_id}/key", headers=AUTH)


def put_config(client: TestClient, body: dict):
    return client.put("/config", json=body, headers=AUTH)


def get_config(client: TestClient) -> dict:
    response = client.get("/config", headers=AUTH)
    assert response.status_code == 200
    return response.json()


def provider_rows(client: TestClient) -> dict:
    response = client.get("/providers", headers=AUTH)
    assert response.status_code == 200
    return {entry["id"]: entry for entry in response.json()["providers"]}


def fill(store: ModelCatalog) -> None:
    for provider_id, endpoint in (("openai", None), ("openai", OLLAMA), ("gemini", None)):
        store.put(provider_id, endpoint, SOME_LISTING)


# ---------------------------------------------------------------------------
# PUT /providers/{id}
# ---------------------------------------------------------------------------


def test_put_provider_saves_every_field(client: TestClient, manager: ConfigManager):
    response = put_provider(
        client,
        "openai",
        {
            "api_key": "ollama",
            "base_url": f"{OLLAMA}/",
            "enabled_models": ["llama3.2:3b", "qwen3"],
            "temperature": 1.2,
            "max_tokens": 2048,
            "max_qps": 3.0,
        },
    )

    assert response.status_code == 200
    saved = reloaded(manager).providers["openai"]
    assert saved.api_key == "ollama"
    assert saved.base_url == OLLAMA
    assert saved.enabled_models == ["llama3.2:3b", "qwen3"]
    assert saved.temperature == 1.2
    assert saved.max_tokens == 2048
    assert saved.max_qps == 3.0


def test_put_provider_answers_with_its_provider_row(client: TestClient):
    response = put_provider(
        client,
        "openai",
        {
            "api_key": "ollama",
            "base_url": OLLAMA,
            "enabled_models": ["llama3.2:3b"],
            "temperature": 1.2,
            "max_tokens": 2048,
            "max_qps": 3.0,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "openai"
    assert body["has_key"] is True
    assert body["base_url"] == OLLAMA
    assert body["enabled_models"] == ["llama3.2:3b"]
    assert body["temperature"] == 1.2
    assert body["max_tokens"] == 2048
    assert body["max_qps"] == 3.0
    assert body == provider_rows(client)["openai"]


def test_every_provider_row_carries_its_settings(client: TestClient, manager: ConfigManager):
    seed(
        manager,
        providers={"anthropic": {"enabled_models": ["claude-opus-5"], "temperature": 0.5}},
    )

    rows = provider_rows(client)

    assert rows["anthropic"]["enabled_models"] == ["claude-opus-5"]
    assert rows["anthropic"]["temperature"] == 0.5
    assert rows["anthropic"]["max_tokens"] == 4000
    assert rows["openai"]["enabled_models"] == []
    assert rows["openai"]["max_tokens"] is None
    assert rows["openai"]["max_qps"] is None


def test_an_empty_key_clears_it(client: TestClient, manager: ConfigManager):
    seed(manager, providers={"openai": {"api_key": KEY}})

    response = put_provider(client, "openai", {"api_key": ""})

    assert response.status_code == 200
    assert response.json()["has_key"] is False
    assert not reloaded(manager).providers["openai"].api_key


def test_an_absent_key_is_left_as_it_is(client: TestClient, manager: ConfigManager):
    seed(manager, providers={"openai": {"api_key": KEY}})

    response = put_provider(client, "openai", {"temperature": 0.5})

    assert response.status_code == 200
    assert response.json()["has_key"] is True
    settings = reloaded(manager).providers["openai"]
    assert settings.api_key == KEY
    assert settings.temperature == 0.5


def test_an_empty_endpoint_is_the_providers_own(client: TestClient, manager: ConfigManager):
    seed(manager, providers={"openai": {"api_key": "ollama", "base_url": OLLAMA}})

    response = put_provider(client, "openai", {"api_key": KEY, "base_url": ""})

    assert response.status_code == 200
    assert response.json()["base_url"] is None
    assert reloaded(manager).providers["openai"].base_url is None


def test_changing_the_endpoint_needs_the_key_again(client: TestClient, manager: ConfigManager):
    """Otherwise anything holding the bearer token could send the saved key to
    a server of its own (#32)."""
    seed(manager, providers={"openai": {"api_key": KEY}})
    before = manager.config_file.read_text(encoding="utf-8")

    response = put_provider(client, "openai", {"base_url": ATTACKER})

    assert response.status_code == 422
    assert "API key" in response.json()["detail"]
    assert manager.config_file.read_text(encoding="utf-8") == before
    assert manager.settings.providers["openai"].base_url is None


# Ported from `test_config_api.py` when `PUT /config`'s per-provider blocks
# were retired (#88): each rule held there, and holds here.


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

    response = put_provider(client, "openai", {"api_key": typed_key, "base_url": ATTACKER})

    assert response.status_code == 200
    settings = reloaded(manager)
    assert settings.providers["openai"].base_url == ATTACKER
    assert (settings.providers["openai"].api_key or None) == saved_key


def test_returning_to_the_providers_own_endpoint_needs_the_key_too(
    client: TestClient, manager: ConfigManager
):
    assert put_provider(
        client, "anthropic", {"api_key": "ollama", "base_url": "http://localhost:11434"}
    ).status_code == 200

    assert put_provider(client, "anthropic", {"base_url": ""}).status_code == 422
    assert manager.settings.providers["anthropic"].base_url == "http://localhost:11434"


def test_the_same_endpoint_typed_again_is_not_a_change(client: TestClient):
    put_provider(client, "openai", {"api_key": "ollama", "base_url": OLLAMA})

    assert put_provider(client, "openai", {"base_url": f"  {OLLAMA}/ "}).status_code == 200


def test_an_endpoint_needs_no_key_when_none_is_saved(
    client: TestClient, manager: ConfigManager
):
    assert put_provider(client, "openai", {"base_url": OLLAMA}).status_code == 200
    assert manager.settings.providers["openai"].base_url == OLLAMA


@pytest.mark.parametrize(
    "base_url", ["localhost:11434", "ftp://example.com", "http://", "/"]
)
def test_an_endpoint_must_be_a_web_url(client: TestClient, base_url: str):
    assert put_provider(client, "openai", {"base_url": base_url}).status_code == 422


def test_model_names_are_trimmed(client: TestClient, manager: ConfigManager):
    """Any name, not only a listed one: a local server's models are in no list."""
    response = put_provider(client, "openai", {"enabled_models": ["  llama3.2:3b "]})

    assert response.status_code == 200
    assert manager.settings.providers["openai"].enabled_models == ["llama3.2:3b"]


def test_a_blank_model_name_is_refused(client: TestClient):
    assert put_provider(client, "gemini", {"enabled_models": ["   "]}).status_code == 422


def test_a_refused_save_keeps_the_catalog(
    client: TestClient, manager: ConfigManager, store: ModelCatalog
):
    seed(manager, providers={"openai": {"api_key": KEY}})
    fill(store)

    assert put_provider(client, "openai", {"base_url": ATTACKER}).status_code == 422

    assert store.get("openai", None) is not None


def test_a_save_that_fails_keeps_the_catalog(
    client: TestClient,
    manager: ConfigManager,
    store: ModelCatalog,
    monkeypatch: pytest.MonkeyPatch,
):
    """The file still holds the old key, so its rows still describe it: the
    catalog is only dropped once the new key is on disk."""
    seed(manager, providers={"openai": {"api_key": KEY}})
    fill(store)
    monkeypatch.setattr(manager, "save_settings", lambda settings: False)

    assert put_provider(client, "openai", {"api_key": "sk-new"}).status_code == 500

    assert store.get("openai", None) is not None


def test_changing_the_endpoint_needs_a_key_that_could_not_be_read_again(
    client: TestClient, manager: ConfigManager, unreadable_key: str
):
    response = put_provider(client, "openai", {"base_url": ATTACKER})

    assert response.status_code == 422
    assert "API key" in response.json()["detail"]
    assert manager.settings.providers["openai"].base_url is None
    assert unreadable_key in manager.config_file.read_text(encoding="utf-8")


def test_an_unrelated_provider_change_keeps_a_key_it_could_not_read(
    client: TestClient, manager: ConfigManager, unreadable_key: str
):
    assert put_provider(client, "openai", {"temperature": 0.9}).status_code == 200

    assert unreadable_key in manager.config_file.read_text(encoding="utf-8")
    assert manager.has_unreadable_key("openai")


@pytest.mark.parametrize("typed", [KEY, ""], ids=["set", "cleared"])
def test_setting_or_clearing_a_key_forgets_one_that_could_not_be_read(
    client: TestClient, manager: ConfigManager, unreadable_key: str, typed: str
):
    assert put_provider(client, "openai", {"api_key": typed}).status_code == 200

    assert unreadable_key not in manager.config_file.read_text(encoding="utf-8")
    assert not manager.has_unreadable_key("openai")
    assert (reloaded(manager).providers["openai"].api_key or "") == typed


@pytest.mark.parametrize(
    "saved, body",
    [
        ({}, {"api_key": "sk-new"}),
        ({"api_key": KEY}, {"api_key": ""}),
        ({}, {"base_url": OLLAMA}),
    ],
    ids=["key-set", "key-cleared", "endpoint-changed"],
)
def test_a_key_or_endpoint_change_drops_that_providers_catalog_rows(
    client: TestClient, manager: ConfigManager, store: ModelCatalog, saved: dict, body: dict
):
    seed(manager, providers={"openai": saved})
    fill(store)

    assert put_provider(client, "openai", body).status_code == 200

    assert store.get("openai", None) is None
    assert store.get("openai", OLLAMA) is None
    assert store.get("gemini", None) is not None


def test_a_change_to_neither_key_nor_endpoint_keeps_the_catalog(
    client: TestClient, manager: ConfigManager, store: ModelCatalog
):
    seed(manager, providers={"openai": {"api_key": KEY}})
    fill(store)

    body = {"enabled_models": ["gpt-5.6-luna"], "temperature": 0.9}
    assert put_provider(client, "openai", body).status_code == 200

    assert store.get("openai", None) is not None


@pytest.mark.parametrize(
    "provider_id, body",
    [
        ("gemini", {"base_url": "http://localhost:8080"}),
        ("anthropic", {"temperature": 1.5}),
    ],
    ids=["gemini-endpoint", "anthropic-temperature"],
)
def test_a_value_the_provider_refuses_is_a_422_and_nothing_is_saved(
    client: TestClient, manager: ConfigManager, provider_id: str, body: dict
):
    seed(manager)
    before = manager.config_file.read_text(encoding="utf-8")

    assert put_provider(client, provider_id, body).status_code == 422

    assert manager.config_file.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# DELETE /providers/{id}/key
# ---------------------------------------------------------------------------


def test_deleting_a_key_clears_it(client: TestClient, manager: ConfigManager):
    seed(manager, providers={"anthropic": {"api_key": KEY, "enabled_models": ["claude-opus-5"]}})

    response = delete_key(client, "anthropic")

    assert response.status_code == 200
    assert response.json()["id"] == "anthropic"
    assert response.json()["has_key"] is False
    saved = reloaded(manager).providers["anthropic"]
    assert not saved.api_key
    assert saved.enabled_models == ["claude-opus-5"], "only the key goes"


def test_deleting_a_key_clears_one_that_could_not_be_read(
    client: TestClient, manager: ConfigManager, unreadable_key: str
):
    assert delete_key(client, "openai").status_code == 200

    assert unreadable_key not in manager.config_file.read_text(encoding="utf-8")
    assert not manager.has_unreadable_key("openai")


def test_deleting_a_key_drops_its_catalog_rows(
    client: TestClient, manager: ConfigManager, store: ModelCatalog
):
    seed(manager, providers={"openai": {"api_key": KEY}})
    fill(store)

    assert delete_key(client, "openai").status_code == 200

    assert store.get("openai", None) is None
    assert store.get("openai", OLLAMA) is None
    assert store.get("gemini", None) is not None


def test_argos_has_no_key_to_delete(client: TestClient):
    assert delete_key(client, "argos").status_code == 422


# ---------------------------------------------------------------------------
# PUT /config: translation_model and answer_model
# ---------------------------------------------------------------------------


def test_the_translation_model_is_saved(client: TestClient, manager: ConfigManager):
    ref = {"provider": "anthropic", "model": "claude-opus-5"}

    response = put_config(client, {"translation_model": ref})

    assert response.status_code == 200
    translation = response.json()["translation"]
    assert translation["model"] == ref
    assert reloaded(manager).translation.model == config_models.ModelRef(**ref)


@pytest.mark.parametrize(
    "body",
    [
        {"openai": {"api_key": "sk-new"}},
        {"anthropic": {"base_url": "http://localhost:11434"}},
        {"preferred_service": "gemini"},
    ],
    ids=["key-block", "endpoint-block", "preferred-service"],
)
def test_the_retired_per_provider_fields_are_refused(
    client: TestClient, manager: ConfigManager, body: dict
):
    """Retired in #88. Ignored, a client older than that would get a 200 for a
    key that was never saved, and go on believing it was."""
    before = manager.config_file.read_text(encoding="utf-8") if manager.config_file.exists() else None

    assert put_config(client, body).status_code == 422

    after = manager.config_file.read_text(encoding="utf-8") if manager.config_file.exists() else None
    assert after == before


def test_a_translation_model_with_an_unknown_provider_is_refused(client: TestClient):
    body = {"translation_model": {"provider": "mistral", "model": "mistral-large"}}

    assert put_config(client, body).status_code == 422


def test_the_answer_model_is_saved(client: TestClient, manager: ConfigManager):
    ref = {"provider": "openai", "model": "gpt-4.1"}

    response = put_config(client, {"answer_model": ref})

    assert response.status_code == 200
    assert response.json()["rag"]["answer_model"] == ref
    assert get_config(client)["rag"]["answer_model"] == ref
    assert reloaded(manager).rag.answer_model == config_models.ModelRef(**ref)


def test_an_answer_model_sent_as_null_is_cleared(client: TestClient, manager: ConfigManager):
    assert put_config(
        client, {"answer_model": {"provider": "openai", "model": "gpt-4.1"}}
    ).status_code == 200

    response = put_config(client, {"answer_model": None})

    assert response.status_code == 200
    assert response.json()["rag"]["answer_model"] is None
    assert reloaded(manager).rag.answer_model is None


def test_an_answer_model_left_out_is_left_as_it_is(client: TestClient, manager: ConfigManager):
    ref = {"provider": "openai", "model": "gpt-4.1"}
    assert put_config(client, {"answer_model": ref}).status_code == 200

    assert put_config(client, {"chat_enabled": True}).status_code == 200

    assert get_config(client)["rag"]["answer_model"] == ref
    assert reloaded(manager).rag.answer_model == config_models.ModelRef(**ref)


# ---------------------------------------------------------------------------
# PUT /config / GET /config: the per-service compatibility layer
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# promotion off Argos
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# added after review
# ---------------------------------------------------------------------------


def test_max_tokens_sent_as_null_goes_back_to_the_providers_default(
    client: TestClient, manager: ConfigManager
):
    """Anthropic's API requires `max_tokens`. `null` means "the built-in
    default", which for Anthropic is 4000 — not `None`, which every request
    would then send until a restart read the file again."""
    assert put_provider(client, "anthropic", {"max_tokens": 8000}).status_code == 200

    response = put_provider(client, "anthropic", {"max_tokens": None})

    assert response.status_code == 200
    assert response.json()["max_tokens"] == 4000
    assert manager.settings.providers["anthropic"].max_tokens == 4000
    assert reloaded(manager).providers["anthropic"].max_tokens == 4000


def test_a_refused_value_keeps_a_key_that_could_not_be_read(
    client: TestClient, manager: ConfigManager, unreadable_key: str
):
    """A key typed over an unreadable one replaces it only if the save goes
    through. A value the registry refuses stops it after the request was
    accepted, so the preserved ciphertext must still be there."""
    response = put_provider(client, "openai", {"api_key": "sk-new", "temperature": 2.5})

    assert response.status_code == 422
    assert manager.has_unreadable_key("openai")
    on_disk = tomlkit.parse(manager.config_file.read_text(encoding="utf-8"))
    assert on_disk["providers"]["openai"]["api_key"] == unreadable_key
