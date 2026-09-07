"""HTTP-level tests for `POST /pdf/export`.

The app is assembled from the `pdf` router alone rather than via
`create_app()`, which keeps the test to the surface it is about — no lifespan,
no CORS, no other routers. It used to be a cost decision too: `create_app()`
imports `routes/translation.py`, which dragged in BabelDOC + torch and turned a
millisecond run into a minute-long one. That is no longer true (see
`test_sidecar_boot.py`), so mounting the full app would now be merely broader,
not slow. The router object under test is the same one `create_app()` mounts.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from desktop_pdf_translator.api import auth
from desktop_pdf_translator.api.routes import pdf as pdf_routes

from conftest import MINIMAL_PDF

TOKEN = "test-token-for-pdf-export"


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "_TOKEN", TOKEN)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(pdf_routes.router)
    # Not used as a context manager on purpose: that would run the sidecar
    # lifespan (settings load, Argos pre-warm, temp sweep), none of which this
    # route touches.
    return TestClient(app)


def _post(
    client: TestClient,
    source: Path | str,
    destination: Path | str,
    protect: Path | str | None = None,
):
    body = {"source_path": str(source), "destination_path": str(destination)}
    if protect is not None:
        body["protect_path"] = str(protect)
    return client.post(
        "/pdf/export",
        json=body,
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


# ---------------------------------------------------------------------------
# Success
# ---------------------------------------------------------------------------


def test_export_returns_the_saved_path(
    client: TestClient, rolling_pdf: Path, tmp_path: Path
):
    destination = tmp_path / "Docs" / "paper_vi.pdf"

    response = _post(client, rolling_pdf, destination)

    assert response.status_code == 200
    body = response.json()
    assert Path(body["saved_path"]) == destination.resolve()
    assert body["bytes_written"] == len(MINIMAL_PDF)
    assert destination.read_bytes() == MINIMAL_PDF


def test_saved_copy_outlives_the_job_temp_dir(
    client: TestClient, rolling_pdf: Path, tmp_path: Path
):
    destination = tmp_path / "paper_vi.pdf"
    assert _post(client, rolling_pdf, destination).status_code == 200

    shutil.rmtree(rolling_pdf.parent)

    assert destination.read_bytes() == MINIMAL_PDF


def test_export_is_repeatable_to_a_second_location(
    client: TestClient, rolling_pdf: Path, tmp_path: Path
):
    """'Save a copy…' after an initial save must not have consumed the source."""
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"

    assert _post(client, rolling_pdf, first).status_code == 200
    assert _post(client, rolling_pdf, second).status_code == 200

    assert first.read_bytes() == second.read_bytes() == MINIMAL_PDF


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def test_missing_source_is_404(client: TestClient, rolling_pdf: Path, tmp_path: Path):
    shutil.rmtree(rolling_pdf.parent)

    response = _post(client, rolling_pdf, tmp_path / "paper_vi.pdf")

    assert response.status_code == 404
    assert "Translate the document again" in response.json()["detail"]


def test_invalid_destination_suffix_is_400(
    client: TestClient, rolling_pdf: Path, tmp_path: Path
):
    response = _post(client, rolling_pdf, tmp_path / "paper_vi.txt")

    assert response.status_code == 400
    assert ".pdf" in response.json()["detail"]


def test_empty_source_is_400(
    client: TestClient, empty_rolling_pdf: Path, tmp_path: Path
):
    response = _post(client, empty_rolling_pdf, tmp_path / "paper_vi.pdf")

    assert response.status_code == 400
    assert not (tmp_path / "paper_vi.pdf").exists()


def test_overwriting_the_opened_document_is_400(
    client: TestClient, rolling_pdf: Path, tmp_path: Path
):
    original = tmp_path / "chapter_vi.pdf"
    original.write_bytes(b"%PDF-1.4\nthe user's document\n")

    response = _post(client, rolling_pdf, original, protect=original)

    assert response.status_code == 400
    assert "document you opened" in response.json()["detail"]
    assert original.read_bytes() == b"%PDF-1.4\nthe user's document\n"


def test_protect_path_is_optional_in_the_request_body(
    client: TestClient, rolling_pdf: Path, tmp_path: Path
):
    response = _post(client, rolling_pdf, tmp_path / "paper_vi.pdf")

    assert response.status_code == 200


def test_permission_failure_is_403(
    client: TestClient, rolling_pdf: Path, tmp_path: Path, deny_copyfile: None
):
    response = _post(client, rolling_pdf, tmp_path / "paper_vi.pdf")

    assert response.status_code == 403


def test_export_requires_the_bearer_token(
    client: TestClient, rolling_pdf: Path, tmp_path: Path
):
    destination = tmp_path / "paper_vi.pdf"

    response = client.post(
        "/pdf/export",
        json={
            "source_path": str(rolling_pdf),
            "destination_path": str(destination),
        },
    )

    assert response.status_code == 401
    assert not destination.exists()


def test_malformed_body_is_422(client: TestClient):
    response = client.post(
        "/pdf/export",
        json={"source_path": "only-one-field.pdf"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert response.status_code == 422


def test_export_route_is_registered_under_the_pdf_prefix():
    """Guards the prefix the frontend hard-codes in `useExportTranslated`."""
    paths = {route.path for route in pdf_routes.router.routes}
    assert "/pdf/export" in paths
