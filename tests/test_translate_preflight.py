"""`POST /translate` refuses a job that can't run, before the job exists.

Two reasons are covered here. A machine with no layout model (#21) used to
accept the job, get minutes into it, and fail from inside chunk 1 with
"BabelDOC processing error in chunk 1: 1" — the stringified `SystemExit`
BabelDOC's asset layer raises — leaving a partial artifact behind. And a page
past the end of the document (#33) opened the progress overlay and then failed.

Only the `translation` router is mounted, so no lifespan runs (see
`test_pdf_export_api.py` for the same reasoning), and `_run_translation` is
stubbed: a real one would import BabelDOC and start a pipeline.
"""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from desktop_pdf_translator.api import auth
from desktop_pdf_translator.api.routes import translation as translation_routes
from desktop_pdf_translator.config import AppSettings, LanguageCode, ModelRef, TranslationService
from desktop_pdf_translator.processors.pdf_pages import PdfInfo
from desktop_pdf_translator.providers.registry import provider
from desktop_pdf_translator.engine_assets import MISSING_ASSETS_MESSAGE

from conftest import MINIMAL_PDF

TOKEN = "test-token-for-translate-preflight"


class _FakeTranslationSettings:
    def __init__(self, preferred: TranslationService):
        self.default_source_lang = LanguageCode.ENGLISH
        self.default_target_lang = LanguageCode.VIETNAMESE
        self.model = ModelRef(provider=preferred, model=provider(preferred.value).default_model)


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
def started(monkeypatch: pytest.MonkeyPatch) -> list:
    """The requests that became jobs. Nothing real runs."""
    payloads: list = []

    async def _noop(job_id: str, payload) -> None:
        payloads.append(payload)

    monkeypatch.setattr(translation_routes, "_run_translation", _noop)
    return payloads


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    path = tmp_path / "paper.pdf"
    path.write_bytes(MINIMAL_PDF)
    return path


@pytest.fixture
def five_pages(tmp_path: Path) -> Path:
    path = tmp_path / "book.pdf"
    with fitz.open() as doc:
        for _ in range(5):
            doc.new_page()
        doc.save(path)
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
    settings = _FakeSettings(preferred, keyed)
    monkeypatch.setattr(translation_routes, "get_settings", lambda: settings)


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


# ---------------------------------------------------------------------------
# Page selections (#33)
# ---------------------------------------------------------------------------


def test_a_selection_starts_the_job(
    client: TestClient, five_pages: Path, monkeypatch: pytest.MonkeyPatch, started
):
    _settings(monkeypatch)
    _engine(monkeypatch, ready=True)
    response = _post(client, five_pages, page_ranges=[[4, 5]])
    assert response.status_code == 202
    assert [p.page_ranges for p in started] == [[(4, 5)]]


def test_a_page_past_the_end_is_refused(
    client: TestClient, five_pages: Path, monkeypatch: pytest.MonkeyPatch
):
    _settings(monkeypatch)
    _engine(monkeypatch, ready=True)
    response = _post(client, five_pages, page_ranges=[[4, 9]])
    assert response.status_code == 422
    assert response.json()["detail"] == "This PDF has 5 pages, so there is no page 9."


def test_a_backwards_range_is_refused(
    client: TestClient, five_pages: Path, monkeypatch: pytest.MonkeyPatch
):
    _settings(monkeypatch)
    _engine(monkeypatch, ready=True)
    response = _post(client, five_pages, page_ranges=[[3, 1]])
    assert response.status_code == 422
    assert "ends before it starts" in response.json()["detail"]


def test_a_bad_selection_is_reported_before_a_missing_engine(
    client: TestClient, five_pages: Path, monkeypatch: pytest.MonkeyPatch
):
    """The selection is something the user can settle in the toolbar; the setup
    screen can't. Reporting 409 first would send them through an install only
    to be refused again."""
    _settings(monkeypatch)
    _engine(monkeypatch, ready=False)
    assert _post(client, five_pages, page_ranges=[[4, 9]]).status_code == 422


def test_the_pair_check_comes_before_the_selection(
    client: TestClient, five_pages: Path, monkeypatch: pytest.MonkeyPatch
):
    _settings(monkeypatch)
    _engine(monkeypatch, ready=True)
    response = _post(
        client, five_pages, page_ranges=[[4, 9]], source_lang="en", target_lang="ja"
    )
    assert response.status_code == 422
    assert "Argos" in response.json()["detail"]


@pytest.mark.parametrize("ranges", [[], [[0, 2]], [[1]], [[1, 2]] * 501])
def test_a_malformed_selection_is_refused_by_the_schema(
    client: TestClient,
    five_pages: Path,
    monkeypatch: pytest.MonkeyPatch,
    ranges,
    started,
):
    _settings(monkeypatch)
    _engine(monkeypatch, ready=True)
    assert _post(client, five_pages, page_ranges=ranges).status_code == 422
    assert started == []


def test_a_file_that_is_not_a_pdf_is_refused(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fake = tmp_path / "notes.pdf"
    fake.write_bytes(b"this is not a PDF")
    _settings(monkeypatch)
    _engine(monkeypatch, ready=True)
    response = _post(client, fake)
    assert response.status_code == 422
    assert response.json()["detail"].startswith("Cannot open PDF file")


def test_a_password_protected_pdf_is_refused(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """MuPDF opens it and reports one page, so the pre-flight used to wave it
    through into a job that could never read it (#68)."""
    locked = tmp_path / "locked.pdf"
    with fitz.open() as doc:
        doc.new_page()
        doc.save(locked, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="open-me")
    _settings(monkeypatch)
    _engine(monkeypatch, ready=True)
    response = _post(client, locked)
    assert response.status_code == 422
    assert "password-protected" in response.json()["detail"]


# ---------------------------------------------------------------------------
# No page or size limit
# ---------------------------------------------------------------------------
#
# These use the real `AppSettings`, not `_FakeSettings`: the point is that
# nothing in the settings caps a translation any more, so a stand-in that
# carries (or lacks) the old fields would prove nothing.


def test_a_long_pdf_with_no_selection_starts_the_job(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, started
):
    """Over 50 pages used to be a 422 that sent the user to the Pages box (#33)."""
    book = tmp_path / "long.pdf"
    with fitz.open() as doc:
        for _ in range(120):
            doc.new_page()
        doc.save(book)
    monkeypatch.setattr(translation_routes, "get_settings", lambda: AppSettings())
    _engine(monkeypatch, ready=True)

    response = _post(client, book)

    assert response.status_code == 202
    assert [p.page_ranges for p in started] == [None]


def test_a_large_file_starts_the_job(
    client: TestClient, five_pages: Path, monkeypatch: pytest.MonkeyPatch, started
):
    """500 MB is far past the 200 MB the setting could be raised to."""
    monkeypatch.setattr(translation_routes, "get_settings", lambda: AppSettings())
    monkeypatch.setattr(
        translation_routes,
        "inspect_pdf",
        lambda path: PdfInfo(page_count=5, size_mb=500.0),
    )
    _engine(monkeypatch, ready=True)

    assert _post(client, five_pages).status_code == 202
    assert len(started) == 1
