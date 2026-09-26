"""`/config`: the model choices, limits, switches and caches.

Keys and endpoints are `/providers`' since #88, and their rules are tested in
`test_provider_config_api.py`, where the tests that stood here for `PUT
/config`'s per-provider blocks were ported. What stays is that a save here
leaves them alone: a key it can't read, and the model catalog.

Only the config router is mounted, so no lifespan runs, and settings live in a
`ConfigManager` under `tmp_path`. Nothing here reaches a provider: the one
function that could, `providers.catalog.list_models`, is replaced by a
recorder (`provider_fakes.FakeLister`).
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


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


def test_every_default_model_is_the_first_suggestion(client: TestClient):
    """A default missing from the suggestions is how `gemini-1.5-flash` stayed
    on offer after Google retired it."""
    services = client.get("/config/options", headers=AUTH).json()["services"]
    defaults = AppSettings()

    for service in services:
        assert service["models"][0] == defaults.model_for(service["code"])


# ---------------------------------------------------------------------------
# the catalog after a save
# ---------------------------------------------------------------------------

SOME_LISTING = Listing(models=(ListedModel(id="gpt-4.1"),))


def fill(store: ModelCatalog) -> None:
    for provider_id, endpoint in (("openai", None), ("openai", OLLAMA), ("gemini", None)):
        store.put(provider_id, endpoint, SOME_LISTING)


@pytest.mark.parametrize(
    "body",
    [
        {"translation_model": {"provider": "openai", "model": "gpt-5.6-luna"}},
        {"answer_model": {"provider": "openai", "model": "gpt-5.6-luna"}},
        {"chat_enabled": False},
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
