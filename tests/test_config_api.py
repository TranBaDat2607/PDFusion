"""`/config`: model names, endpoints, and the key that goes with an endpoint (#32).

Only the config router is mounted, so no lifespan runs, and settings live in a
`ConfigManager` under `tmp_path`. Nothing here reaches a provider:
`_credentials_work` is replaced by a recorder, and `_probe_kwargs` — what it
would hand `TranslatorFactory` — is tested on its own.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from desktop_pdf_translator.api import auth
from desktop_pdf_translator.api.routes import config as config_routes
from desktop_pdf_translator.config import AppSettings, TranslationService
from desktop_pdf_translator.config.manager import ConfigManager

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
    return manager


class _Probe:
    """Stands in for `_credentials_work`, recording what would be probed."""

    def __init__(self) -> None:
        self.calls: List[Tuple[TranslationService, Dict[str, Any]]] = []
        self.result = (True, "Configuration is valid")

    async def __call__(self, service: TranslationService, service_config: dict):
        self.calls.append((service, dict(service_config)))
        return self.result


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> _Probe:
    probe = _Probe()
    monkeypatch.setattr(config_routes, "_credentials_work", probe)
    return probe


@pytest.fixture
def client(manager: ConfigManager, probe: _Probe) -> TestClient:
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
    assert reloaded.openai.base_url == OLLAMA
    assert reloaded.openai.api_key == "ollama"


def test_the_promotion_probe_checks_the_new_endpoint(client: TestClient, probe: _Probe):
    put(client, {"openai": {"api_key": "ollama", "base_url": OLLAMA}})

    assert probe.calls[0][1]["base_url"] == OLLAMA


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
    assert manager.settings.openai.base_url is None


def test_returning_to_the_provider_endpoint_needs_the_key_too(
    client: TestClient, manager: ConfigManager
):
    put(client, {"anthropic": {"api_key": "ollama", "base_url": "http://localhost:11434"}})

    assert put(client, {"anthropic": {"base_url": ""}}).status_code == 422
    assert manager.settings.anthropic.base_url == "http://localhost:11434"


def test_the_same_endpoint_typed_again_is_not_a_change(client: TestClient):
    put(client, {"openai": {"api_key": "ollama", "base_url": OLLAMA}})

    assert put(client, {"openai": {"base_url": f"  {OLLAMA}/ "}}).status_code == 200


def test_an_endpoint_needs_no_key_when_none_is_saved(
    client: TestClient, manager: ConfigManager
):
    assert put(client, {"openai": {"base_url": OLLAMA}}).status_code == 200
    assert manager.settings.openai.base_url == OLLAMA


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
    assert manager.settings.openai.model == "llama3.2:3b"


def test_a_blank_model_is_refused(client: TestClient):
    assert put(client, {"gemini": {"model": "   "}}).status_code == 422


def test_every_default_model_is_the_first_suggestion(client: TestClient):
    """A default missing from the suggestions is how `gemini-1.5-flash` stayed
    on offer after Google retired it."""
    services = client.get("/config/options", headers=AUTH).json()["services"]
    defaults = AppSettings()

    for service in services:
        assert service["models"][0] == getattr(defaults, service["code"]).model


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_checks_the_saved_key_against_the_saved_endpoint(
    client: TestClient, probe: _Probe
):
    put(
        client,
        {"openai": {"api_key": "ollama", "base_url": OLLAMA, "model": "llama3.2"}},
    )
    probe.calls.clear()

    response = validate(client, {"service": "openai"})

    assert response.json() == {"valid": True, "message": "Configuration is valid"}
    assert probe.calls == [
        (
            TranslationService.OPENAI,
            {"model": "llama3.2", "base_url": OLLAMA, "api_key": "ollama"},
        )
    ]


def test_validate_never_sends_the_saved_key_to_another_endpoint(
    client: TestClient, probe: _Probe
):
    put(client, {"openai": {"api_key": KEY}})
    probe.calls.clear()

    response = validate(
        client, {"service": "openai", "base_url": "https://attacker.example/v1"}
    )

    assert response.status_code == 422
    assert probe.calls == []


def test_validate_checks_any_endpoint_with_a_typed_key(
    client: TestClient, probe: _Probe
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
    assert probe.calls == [
        (
            TranslationService.ANTHROPIC,
            {"model": "qwen3", "base_url": "http://localhost:11434", "api_key": "ollama"},
        )
    ]


def test_validate_without_any_key_does_not_probe(client: TestClient, probe: _Probe):
    response = validate(client, {"service": "gemini"})

    assert response.json()["valid"] is False
    assert probe.calls == []


def test_a_probe_names_the_endpoint_even_when_it_is_the_default():
    """The factory starts from the saved settings, so a probe that left
    `base_url` out would check the saved endpoint instead."""
    assert config_routes._probe_kwargs(
        {"api_key": KEY, "model": "gpt-4.1", "base_url": None, "temperature": 0.3}
    ) == {"api_key": KEY, "base_url": None, "model": "gpt-4.1"}
    assert config_routes._probe_kwargs({"api_key": KEY, "model": ""}) == {"api_key": KEY}
