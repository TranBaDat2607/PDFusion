"""`/setup` — installing the offline engine, and reporting it (#21).

Only the `setup` router is mounted (see `test_pdf_export_api.py` for the same
reasoning), with `_install_engine` replaced so nothing is downloaded. The
`TestClient` *is* used as a context manager here, unlike the other API suites:
the install runs as a background task, and without a persistent portal it would
not survive to the next request — which is the exact property under test.
"""

from __future__ import annotations

import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from desktop_pdf_translator.api import auth
from desktop_pdf_translator.api.routes import setup as setup_routes
from desktop_pdf_translator.engine_assets import AssetGroupStatus

TOKEN = "test-token-for-setup"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _groups(ready: bool) -> list[AssetGroupStatus]:
    return [
        AssetGroupStatus(
            id="babeldoc",
            label="Layout engine",
            ready=ready,
            present=183 if ready else 19,
            total=183,
            detail="",
        ),
        AssetGroupStatus(
            id="argos", label="Offline translator", ready=True, present=1, total=1, detail=""
        ),
    ]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "_TOKEN", TOKEN)
    # The install state is a module global by design — one cache directory, one
    # download — so each test gets a fresh one.
    monkeypatch.setattr(setup_routes, "_install", setup_routes._Install())
    monkeypatch.setattr(setup_routes, "engine_status", lambda: _groups(False))
    monkeypatch.setattr(setup_routes, "bundled_babeldoc_zip", lambda: None)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(setup_routes.router)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def blocking_install(monkeypatch: pytest.MonkeyPatch):
    """An install that hangs until released, and counts how often it started."""
    release = threading.Event()
    starts: list[str] = []

    def fake(phase) -> None:
        starts.append(phase.noun)
        phase.noun = "fonts"
        release.wait(timeout=10)

    monkeypatch.setattr(setup_routes, "_install_engine", fake)
    yield release, starts
    release.set()


# ---------------------------------------------------------------------------


def test_status_reports_what_is_missing(client: TestClient):
    body = client.get("/setup/status", headers=AUTH).json()
    assert body["ready"] is False
    assert body["groups"][0]["present"] == 19
    assert body["install"] == {"running": False, "stage": None, "error": None}


def test_status_needs_the_token(client: TestClient):
    assert client.get("/setup/status").status_code == 401


def test_starting_an_install_reports_it_as_running(client: TestClient, blocking_install):
    body = client.post("/setup/engine", headers=AUTH).json()
    assert body["install"]["running"] is True
    # The phase is named, so the screen has something truer than a spinner.
    assert client.get("/setup/status", headers=AUTH).json()["install"]["stage"]


def test_a_second_request_joins_the_running_install(client: TestClient, blocking_install):
    _release, starts = blocking_install
    client.post("/setup/engine", headers=AUTH)
    client.post("/setup/engine", headers=AUTH)
    # Two downloads writing the same cache paths is how a half-written font
    # ends up passing the existence check the pre-flight relies on.
    assert len(starts) == 1


def test_a_failed_install_is_still_reported_after_it_ends(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """The failure outlives the install. A client that reloaded mid-install
    polls once and finds out why nothing was installed, instead of seeing an
    idle screen that looks like it was never asked."""

    def fake(phase) -> None:
        raise RuntimeError("no network, and no bundled copy")

    monkeypatch.setattr(setup_routes, "_install_engine", fake)
    client.post("/setup/engine", headers=AUTH)

    for _ in range(100):
        install = client.get("/setup/status", headers=AUTH).json()["install"]
        if not install["running"]:
            break
    assert install["running"] is False
    assert install["error"] == "no network, and no bundled copy"


def test_a_finished_install_can_be_retried(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    starts: list[int] = []

    def fake(phase) -> None:
        starts.append(1)

    monkeypatch.setattr(setup_routes, "_install_engine", fake)
    for _ in range(2):
        client.post("/setup/engine", headers=AUTH)
        for _ in range(100):
            if not client.get("/setup/status", headers=AUTH).json()["install"]["running"]:
                break
    assert len(starts) == 2
