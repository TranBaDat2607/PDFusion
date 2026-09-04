"""Which origins the sidecar's CORS allowlist accepts (#17).

Kept out of `test_config_security.py` on purpose: that suite imports only
`config/` + `utils/` and runs in a fraction of a second, while anything under
`desktop_pdf_translator.api` drags in BabelDOC + torch via the package
`__init__`. `test_pdf_export_api.py` already pays that cost in the same run, so
this file adds no wall-clock time — but it would slow the encryption suite down
by ~13s if folded into it.

The rule under test is a two-sided handshake: the Tauri shell sets
`PDFUSION_DEV_ORIGINS` (`sidecar.rs:dev_origins_flag`) and the sidecar honours
it. The `sys.frozen` fallback only covers a sidecar started without the shell.
"""

from __future__ import annotations

import sys

import pytest

from desktop_pdf_translator.api.server import _allowed_origins

VITE = "http://localhost:1420"
TAURI = "http://tauri.localhost"


@pytest.fixture(autouse=True)
def _no_inherited_signal(monkeypatch: pytest.MonkeyPatch):
    """Start every case from "the shell said nothing", so a developer with the
    variable exported doesn't quietly pass the tests that assert it's absent."""
    monkeypatch.delenv("PDFUSION_DEV_ORIGINS", raising=False)


def _frozen(monkeypatch: pytest.MonkeyPatch, value: bool) -> None:
    if value:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
    else:
        monkeypatch.delattr(sys, "frozen", raising=False)


def test_the_tauri_protocol_origins_are_always_allowed(monkeypatch: pytest.MonkeyPatch):
    """WebView2 presents the custom protocol as `http://tauri.localhost`;
    WebKit uses `tauri://localhost`. Both ship, so both are listed."""
    _frozen(monkeypatch, True)
    origins = _allowed_origins()
    assert TAURI in origins
    assert "https://tauri.localhost" in origins
    assert "tauri://localhost" in origins


def test_the_shell_can_ask_for_the_dev_origins(monkeypatch: pytest.MonkeyPatch):
    """The regression this is about: `pnpm tauri dev` prefers a staged
    PyInstaller sidecar over local Python, so a *frozen* sidecar routinely
    serves a *Vite-hosted* webview. Judging by `sys.frozen` alone rejected every
    request that app made."""
    monkeypatch.setenv("PDFUSION_DEV_ORIGINS", "1")
    _frozen(monkeypatch, True)
    assert VITE in _allowed_origins()


def test_the_shell_can_refuse_them_even_when_not_frozen(
    monkeypatch: pytest.MonkeyPatch,
):
    """The shell sends "0" explicitly rather than omitting the variable, so a
    stray `PDFUSION_DEV_ORIGINS=1` in the environment can't be inherited by an
    installed app."""
    monkeypatch.setenv("PDFUSION_DEV_ORIGINS", "0")
    _frozen(monkeypatch, False)
    assert VITE not in _allowed_origins()
    assert TAURI in _allowed_origins()


def test_without_the_shell_a_bundled_sidecar_refuses_them(
    monkeypatch: pytest.MonkeyPatch,
):
    """No signal means nobody spawned us — fall back to `sys.frozen`. A bundled
    exe run by hand has no dev server in front of it."""
    _frozen(monkeypatch, True)
    assert VITE not in _allowed_origins()


def test_without_the_shell_a_source_run_allows_them(monkeypatch: pytest.MonkeyPatch):
    """`python main.py` for backend debugging, with the frontend on `pnpm dev`."""
    _frozen(monkeypatch, False)
    assert VITE in _allowed_origins()
    assert "http://127.0.0.1:1420" in _allowed_origins()


def test_an_unrecognised_signal_is_not_a_yes(monkeypatch: pytest.MonkeyPatch):
    """Only "1" enables them; anything else is read as a refusal rather than as
    "unset", so a malformed value can't loosen the allowlist."""
    monkeypatch.setenv("PDFUSION_DEV_ORIGINS", "true")
    _frozen(monkeypatch, False)
    assert VITE not in _allowed_origins()
