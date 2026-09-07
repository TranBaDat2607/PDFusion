"""`POST /translate` refuses a job the engine can't actually run (#21).

Before this, a machine with no layout model accepted the job, got minutes into
it, and failed from inside chunk 1 with "BabelDOC processing error in chunk 1:
1" — the stringified `SystemExit` BabelDOC's asset layer raises — leaving a
partial artifact behind.

Only the `translation` router is mounted, so no lifespan runs (see
`test_pdf_export_api.py` for the same reasoning), and `_run_translation` is
stubbed: a real one would import BabelDOC and start a pipeline.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from desktop_pdf_translator.api import auth
from desktop_pdf_translator.api.routes import translation as translation_routes
from desktop_pdf_translator.config import LanguageCode, TranslationService
from desktop_pdf_translator.engine_assets import MISSING_ASSETS_MESSAGE

from conftest import MINIMAL_PDF

TOKEN = "test-token-for-translate-preflight"


class _FakeTranslationSettings:
    def __init__(self, preferred: TranslationService):
        self.default_source_lang = LanguageCode.ENGLISH
        self.default_target_lang = LanguageCode.VIETNAMESE
        self.preferred_service = preferred


class _FakeSettings:
    """Only the surface the pre-flight touches, so the tests don't depend on
    whatever config.toml the developer happens to have."""

    def __init__(self, preferred: TranslationService, keyed: tuple[TranslationService, ...]):
        self.translation = _FakeTranslationSettings(preferred)
        self._keyed = set(keyed)

    def has_api_key(self, service: TranslationService) -> bool:
        return service == TranslationService.ARGOS or service in self._keyed


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "_TOKEN", TOKEN)


@pytest.fixture(autouse=True)
def _no_real_jobs(monkeypatch: pytest.MonkeyPatch):
    async def _noop(job_id: str, payload) -> None:
        return None

    monkeypatch.setattr(translation_routes, "_run_translation", _noop)


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    path = tmp_path / "paper.pdf"
    path.write_bytes(MINIMAL_PDF)
    return path


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(translation_routes.router)
    return TestClient(app)


def _settings(
    monkeypatch: pytest.MonkeyPatch,
    preferred: TranslationService = TranslationService.ARGOS,
    keyed: tuple[TranslationService, ...] = (),
):
    monkeypatch.setattr(
        translation_routes, "get_settings", lambda: _FakeSettings(preferred, keyed)
    )


def _engine(monkeypatch: pytest.MonkeyPatch, ready: bool) -> list[str | None]:
    """Stub `engine_ready`, recording the service it was asked about."""
    asked: list[str | None] = []

    def fake(service=None):
        asked.append(service)
        return ready

    monkeypatch.setattr(translation_routes, "engine_ready", fake)
    return asked


def _post(client: TestClient, pdf: Path, **body):
    return client.post(
        "/translate",
        json={"file_path": str(pdf), **body},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


# ---------------------------------------------------------------------------


def test_missing_assets_are_refused_before_the_job_exists(
    client: TestClient, pdf: Path, monkeypatch: pytest.MonkeyPatch
):
    _settings(monkeypatch)
    _engine(monkeypatch, ready=False)
    response = _post(client, pdf)
    assert response.status_code == 409
    assert response.json()["detail"] == MISSING_ASSETS_MESSAGE


def test_an_installed_engine_starts_the_job(
    client: TestClient, pdf: Path, monkeypatch: pytest.MonkeyPatch
):
    _settings(monkeypatch)
    _engine(monkeypatch, ready=True)
    response = _post(client, pdf)
    assert response.status_code == 202
    assert response.json()["job_id"]


def test_the_pair_check_still_wins(
    client: TestClient, pdf: Path, monkeypatch: pytest.MonkeyPatch
):
    """Two pre-flights, and the order matters: "Argos can't do English to
    Japanese" tells the user something a setup screen never will, so an
    unsupported pair must not be reported as a missing install."""
    _settings(monkeypatch)
    _engine(monkeypatch, ready=False)
    response = _post(client, pdf, source_lang="en", target_lang="ja")
    assert response.status_code == 422
    assert "Argos" in response.json()["detail"]


def test_the_engine_is_checked_against_the_effective_service(
    client: TestClient, pdf: Path, monkeypatch: pytest.MonkeyPatch
):
    """A request naming OpenAI with no key configured is silently an Argos run,
    so the Argos pack is what it actually needs. Asking about the *requested*
    service would wave it through and fail mid-job."""
    _settings(monkeypatch, preferred=TranslationService.OPENAI, keyed=())
    asked = _engine(monkeypatch, ready=True)
    _post(client, pdf, service="openai")
    assert asked == ["argos"]


def test_a_keyed_service_is_checked_as_itself(
    client: TestClient, pdf: Path, monkeypatch: pytest.MonkeyPatch
):
    _settings(
        monkeypatch,
        preferred=TranslationService.OPENAI,
        keyed=(TranslationService.OPENAI,),
    )
    asked = _engine(monkeypatch, ready=True)
    _post(client, pdf, service="openai")
    assert asked == ["openai"]
