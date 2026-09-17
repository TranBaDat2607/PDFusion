"""`POST /translate/estimate` — the token count the toolbar shows before an
LLM run (#33).

Only the `translation` router is mounted, as in `test_translate_preflight.py`,
and settings are a stand-in so the developer's `config.toml` can't leak in.
The PDFs are generated with PyMuPDF.
"""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from desktop_pdf_translator.api import auth
from desktop_pdf_translator.api.routes import translation as translation_routes
from desktop_pdf_translator.config import LanguageCode, TranslationService
from desktop_pdf_translator.translators.usage_estimate import estimate_tokens

TOKEN = "test-token-for-translate-estimate"
LINE = "A sentence that a translator would be sent on its own."


class _FakeTranslationSettings:
    default_source_lang = LanguageCode.ENGLISH
    default_target_lang = LanguageCode.VIETNAMESE
    preferred_service = TranslationService.OPENAI
    max_pages = 50
    max_file_size_mb = 50.0
    min_text_length = 5


class _FakeSettings:
    def __init__(self):
        self.translation = _FakeTranslationSettings()


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "_TOKEN", TOKEN)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> _FakeSettings:
    fake = _FakeSettings()
    monkeypatch.setattr(translation_routes, "get_settings", lambda: fake)
    return fake


@pytest.fixture
def book(tmp_path: Path) -> Path:
    """Six pages, each with one paragraph of LINE."""
    path = tmp_path / "book.pdf"
    with fitz.open() as doc:
        for _ in range(6):
            doc.new_page().insert_text((72, 72), LINE)
        doc.save(path)
    return path


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(translation_routes.router)
    return TestClient(app)


def _estimate(client: TestClient, pdf: Path, **body):
    return client.post(
        "/translate/estimate",
        json={"file_path": str(pdf), **body},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


def test_the_whole_document_is_estimated(client, book, settings):
    response = _estimate(client, book)
    assert response.status_code == 200
    body = response.json()
    expected = estimate_tokens(
        paragraphs=6, cjk_chars=0, other_chars=6 * len(LINE), target_lang="vi"
    )
    assert body == {
        "page_count": 6,
        "pages_selected": 6,
        "paragraphs": 6,
        "input_tokens": expected.input_tokens,
        "output_tokens": expected.output_tokens,
    }


def test_a_selection_is_estimated_on_its_own(client, book, settings):
    body = _estimate(client, book, page_ranges=[[2, 3]]).json()
    assert (body["page_count"], body["pages_selected"], body["paragraphs"]) == (6, 2, 2)


def test_the_target_language_changes_the_prompt(client, book, settings):
    vietnamese = _estimate(client, book).json()
    japanese = _estimate(client, book, target_lang="ja").json()
    assert japanese["input_tokens"] < vietnamese["input_tokens"]
    assert japanese["output_tokens"] == vietnamese["output_tokens"]


def test_the_page_limit_does_not_hide_the_estimate(client, book, settings):
    """Over the limit, Translate offers the pages that fit. The estimate still
    describes what is selected."""
    settings.translation.max_pages = 2
    assert _estimate(client, book).json()["pages_selected"] == 6


def test_a_file_too_big_to_translate_is_not_read(client, book, settings, monkeypatch):
    settings.translation.max_file_size_mb = 0.0001
    read = []
    monkeypatch.setattr(
        translation_routes, "text_stats", lambda *a, **k: read.append(a)
    )
    response = _estimate(client, book)
    assert response.status_code == 422
    assert "MB" in response.json()["detail"]
    assert read == []


def test_a_page_past_the_end_is_refused(client, book, settings):
    response = _estimate(client, book, page_ranges=[[5, 9]])
    assert response.status_code == 422
    assert response.json()["detail"] == "This PDF has 6 pages, so there is no page 9."


def test_a_missing_file_is_refused(client, tmp_path, settings):
    assert _estimate(client, tmp_path / "gone.pdf").status_code == 400


def test_an_unreadable_file_is_refused(client, tmp_path, settings):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf")
    response = _estimate(client, broken)
    assert response.status_code == 422
    assert response.json()["detail"].startswith("Cannot open PDF file")


def test_the_estimate_needs_the_token(client, book, settings):
    response = client.post("/translate/estimate", json={"file_path": str(book)})
    assert response.status_code == 401
