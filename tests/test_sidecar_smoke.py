"""End-to-end: start a sidecar, read its READY line, use the token.

`test_sidecar_boot.py` asserts the *import* graph stays cheap; this asserts the
process actually comes up and answers. The two failures it exists to catch:

* a `ModuleNotFoundError` from the PyInstaller `excludes` list — invisible in a
  source run, fatal in the shipped `.msi`, and until now only discoverable by
  installing one;
* a broken handshake. The Rust shell parses exactly one line of stdout and then
  waits out `READY_TIMEOUT` (90 s) plus `HEALTH_TIMEOUT` (30 s) before saying
  anything, so "the app sits on a spinner for two minutes" is what a regression
  here looks like from the outside.

**Marked `smoke` and excluded from the default run.** Every other suite in
`tests/` is in-process and the whole set finishes in about a second; this one
spawns real interpreters and costs tens of seconds. Run it with
`python -m pytest tests -m smoke`, which is what CI does in a job of its own.

The source-mode parameter runs anywhere. The frozen-exe one skips unless
`build-sidecar.ps1` has actually been run — a `-Stub` placeholder is 0 bytes and
is skipped by the same check.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.smoke

REPO_ROOT = Path(__file__).resolve().parents[1]

# The Rust shell allows 90 s for this line, sized for the PyInstaller one-dir
# bootloader paging thousands of files past Defender on a cold first launch.
READY_TIMEOUT_S = 120.0

READY_RE = re.compile(r"^READY port=(\d+) token=(\S+)$")


def staged_sidecar_exe() -> Path | None:
    """The exe `build-sidecar.ps1` leaves behind, in either location it writes:
    PyInstaller's own `dist/`, or the triple-suffixed copy Tauri bundles."""
    candidates = [REPO_ROOT / "dist" / "pdfusion-sidecar" / "pdfusion-sidecar.exe"]
    candidates += sorted(
        (REPO_ROOT / "desktop" / "src-tauri" / "binaries").glob(
            "pdfusion-sidecar-*.exe"
        )
    )
    for path in candidates:
        # A `-Stub` placeholder is a real file of zero bytes.
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


@dataclass
class Sidecar:
    process: subprocess.Popen
    port: int
    token: str

    def get(self, path: str, token: str | None = "") -> tuple[int, dict]:
        """`token=""` means "the real one"; `None` means send no header."""
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if token is not None:
            request.add_header("Authorization", f"Bearer {token or self.token}")
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, {}


def spawn(command: list[str], env_overrides: dict) -> subprocess.Popen:
    return subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, **env_overrides},
        cwd=str(REPO_ROOT),
    )


def await_ready(process: subprocess.Popen) -> Sidecar:
    """Block until the handshake line arrives.

    stdout is read on a thread: `readline()` has no timeout, so a sidecar that
    dies before printing would hang the suite instead of failing it.
    """
    line: list[str] = []

    def read_first_line() -> None:
        assert process.stdout is not None
        line.append(process.stdout.readline())

    reader = threading.Thread(target=read_first_line, daemon=True)
    reader.start()
    reader.join(timeout=READY_TIMEOUT_S)

    if reader.is_alive():
        process.kill()
        pytest.fail(f"no handshake line within {READY_TIMEOUT_S:.0f}s")

    match = READY_RE.match((line[0] if line else "").strip())
    if match is None:
        process.kill()
        _, stderr = process.communicate(timeout=30)
        pytest.fail(
            f"first stdout line was not a READY handshake: {line!r}\n"
            f"--- sidecar stderr ---\n{stderr}"
        )

    return Sidecar(process, int(match.group(1)), match.group(2))


def launch_args(mode: str, home: Path) -> tuple[list[str], dict]:
    """Command + environment for one launch mode.

    The environment keeps the run out of the developer's real
    `~/AppData/Local/PDFusion` — the lifespan creates cache directories there
    and GCs the paragraph cache.
    """
    env = {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "PDFUSION_ARGOS_DEBUG": "0",
    }
    if mode == "source":
        return (
            [sys.executable, "-m", "desktop_pdf_translator.api.server"],
            {**env, "PYTHONPATH": str(REPO_ROOT / "src")},
        )

    exe = staged_sidecar_exe()
    if exe is None:
        pytest.skip("no built sidecar staged — run ./build-sidecar.ps1")
    return [str(exe)], env


@pytest.fixture(scope="module", params=["source", "frozen"])
def sidecar(request, tmp_path_factory):
    """One sidecar per launch mode, shared by every test in the module.

    Module-scoped on purpose: a per-test spawn made this file cost minutes,
    and none of the assertions below mutate server state.
    """
    home = tmp_path_factory.mktemp(f"home-{request.param}")
    command, env = launch_args(request.param, home)
    process = spawn(command, env)
    started = await_ready(process)
    try:
        yield started
    finally:
        process.kill()
        process.wait(timeout=30)


# ---------------------------------------------------------------------------


def test_the_handshake_announces_a_usable_port_and_token(sidecar: Sidecar):
    assert 1024 < sidecar.port < 65536
    # URL-safe base64 of 32 bytes.
    assert len(sidecar.token) >= 32
    assert re.fullmatch(r"[A-Za-z0-9_-]+", sidecar.token)


def test_health_answers_without_a_token(sidecar: Sidecar):
    """The Rust shell's liveness probe. Requiring auth here would make a token
    mismatch indistinguishable from a dead process."""
    status, _ = sidecar.get("/health", token=None)
    assert status == 200


def test_the_announced_token_is_the_one_the_api_accepts(sidecar: Sidecar):
    """`/auth/ping` is what the shell polls after READY; it confirms the token
    survived the handshake, not just that the process is alive."""
    status, body = sidecar.get("/auth/ping")
    assert status == 200
    assert body == {"ok": True}


def test_an_unauthenticated_request_is_refused(sidecar: Sidecar):
    status, _ = sidecar.get("/auth/ping", token=None)
    assert status in (401, 403)


def test_a_wrong_token_is_refused(sidecar: Sidecar):
    status, _ = sidecar.get("/auth/ping", token="not-the-token")
    assert status in (401, 403)


def test_a_real_route_is_served_past_the_middleware(sidecar: Sidecar):
    """One route beyond the two probes wired into `create_app`, so the smoke
    test covers app construction and the config layer behind it."""
    status, body = sidecar.get("/config")
    assert status == 200
    assert "translation" in body


def test_the_setup_status_route_answers_without_the_engine_installed(
    sidecar: Sidecar,
):
    """`/setup/status` is the first thing the frontend's `EngineGate` calls,
    and it is stat-only by design — it must not need BabelDOC importable."""
    status, body = sidecar.get("/setup/status")
    assert status == 200
    assert "ready" in body


def test_the_sidecar_is_still_running_after_serving_requests(sidecar: Sidecar):
    sidecar.get("/auth/ping")
    assert sidecar.process.poll() is None


def test_the_token_never_appears_on_stderr(tmp_path: Path):
    """The token is `print`ed to stdout, not logged — `redact_ready_line` keeps
    it out of the Rust shell's stderr relay, and this keeps it out at the
    source, where any file logger added later would pick it up.

    Spawns its own sidecar rather than using the shared one: reading stderr to
    EOF requires ending the process.
    """
    command, env = launch_args("source", tmp_path)
    process = spawn(command, env)
    started = await_ready(process)
    started.get("/auth/ping")

    process.kill()
    _, stderr = process.communicate(timeout=30)
    assert started.token not in stderr
