"""Offline-engine setup — install the translation assets, with progress.

`engine_assets` says what is missing; this puts it on disk. Three things shape
the implementation:

**BabelDOC reports failure by calling `exit(1)`.** Every downloader in
`babeldoc/assets/assets.py` does, so a failure arrives as `SystemExit(1)` and
carries no message. Worse, BabelDOC's own sync wrappers (`warmup()`,
`restore_offline_assets_package()`) run the coroutine through
`run_in_another_thread`, where Python's `threading.excepthook` swallows
`SystemExit` outright and the call just returns `None` — the failure then
resurfaces much later as a `TypeError` unpacking that `None`. So this module
only ever calls the `_async` variants, under an `asyncio.run` of its own, inside
a `try` that catches `SystemExit` explicitly.

**There are no progress hooks.** Not in BabelDOC's downloaders, not in
`argostranslate`'s. Progress is therefore observed rather than reported:
`GET /setup/status` counts how much of the manifest is on disk.

**This is polled, not streamed** — the one place in the sidecar that is. The
other long jobs follow the SSE pattern in `api/jobs.py`, where `stream()`
discards a job as soon as its consumer detaches. That is right for a translate
(one job per click, the client owns it) and wrong here: the install is a
process-wide singleton that runs for minutes, and a webview reload mid-install
would lose the only handle to it — leaving the next Install click to start a
*second* download into the same cache directory. Polling a status endpoint
re-attaches to a running install for free, which is the property that matters.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional

from fastapi import APIRouter, Depends

from ...engine_assets import (
    bundled_babeldoc_zip,
    describe_asset_failure,
    engine_status,
)
from ..auth import require_token
from ..schemas import EngineInstallState, EngineStatusResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/setup", tags=["setup"], dependencies=[Depends(require_token)])


@dataclass
class _Phase:
    """What the install thread is doing, read by `/setup/status` on the loop.

    A plain string assignment written by one thread and read by another — no
    lock, because CPython attribute assignment is atomic and a reader that sees
    the previous phase for one poll costs nothing.

    The value is a noun phrase, so it reads correctly both as progress
    ("Installing fonts") and as failure ("failed while fetching fonts").
    """

    noun: str = "the translation engine"


@dataclass
class _Install:
    task: Optional[asyncio.Task] = None
    phase: _Phase = field(default_factory=_Phase)
    error: Optional[str] = None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()


# Process-wide, because the thing it guards is: one cache directory, one set of
# files, one download. The lock only covers the swap in `start_setup`.
_lock = threading.Lock()
_install = _Install()


# ---------------------------------------------------------------------------
# Blocking install steps (worker thread)
# ---------------------------------------------------------------------------


async def _download_babeldoc_assets(phase: _Phase) -> None:
    """`assets.async_warmup()`, unrolled so a failure can name its phase.

    One `httpx.AsyncClient` for the whole run, as `async_warmup` does — the
    per-file helpers each open their own when passed `None`, and the font and
    cmap phases fan out to 34 and 146 of them.
    """
    import httpx
    from babeldoc.assets import assets
    from tiktoken import encoding_for_model

    phase.noun = "tokenizer data"
    encoding_for_model("gpt-4o")

    async with httpx.AsyncClient() as client:
        phase.noun = "the page-layout model"
        await assets.get_doclayout_onnx_model_path_async(client)
        phase.noun = "the table-detection model"
        await assets.get_table_detection_rapidocr_model_path_async(client)
        phase.noun = "fonts"
        await assets.download_all_fonts_async(client)
        phase.noun = "character maps"
        await assets.download_all_cmaps_async(client)


def _install_engine(phase: _Phase) -> None:
    """Blocking. Raises with a message already written for the user."""
    from babeldoc.assets import assets

    from ...translators.argos_translator import _ensure_en_vi_installed

    restored = False
    bundled = bundled_babeldoc_zip()
    if bundled is not None:
        phase.noun = "the bundled engine files"
        try:
            asyncio.run(assets.restore_offline_assets_package_async(bundled))
            restored = True
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            # A zip built against a different babeldoc version, or a corrupt
            # one. Downloading is the honest fallback — refusing to set up
            # because a shipped file went stale would be worse.
            logger.warning(
                "Bundled BabelDOC assets could not be restored (%s); downloading instead",
                exc,
            )

    if not restored:
        try:
            asyncio.run(_download_babeldoc_assets(phase))
        except (Exception, SystemExit) as exc:
            logger.warning(
                "BabelDOC asset install failed while fetching %s: %s", phase.noun, exc
            )
            raise RuntimeError(describe_asset_failure(phase.noun)) from exc

    phase.noun = "the offline translator"
    try:
        _ensure_en_vi_installed()
    except (Exception, SystemExit) as exc:
        logger.warning("Argos pack install failed: %s", exc)
        raise RuntimeError(describe_asset_failure(phase.noun)) from exc


async def _run_install(install: _Install) -> None:
    try:
        await asyncio.to_thread(_install_engine, install.phase)
    except Exception as exc:  # noqa: BLE001 — the message is already user-facing
        logger.exception("Engine setup failed")
        install.error = str(exc)
    else:
        logger.info("Engine setup complete")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _snapshot() -> EngineStatusResponse:
    """Blocking: `engine_status()` is a stat per manifest entry, and its *first*
    call also imports `babeldoc.assets.assets` (~310 ms, httpx + tenacity).
    Callers run it through `asyncio.to_thread` — this is hit on every app start,
    when the `engine-warm` thread is already contending for the GIL."""
    groups = engine_status()
    return EngineStatusResponse(
        ready=all(g.ready for g in groups),
        groups=[g.to_dict() for g in groups],
        bundled=bundled_babeldoc_zip() is not None,
        install=EngineInstallState(
            running=_install.running,
            stage=f"Installing {_install.phase.noun}" if _install.running else None,
            error=_install.error,
        ),
    )


@router.get("/status", response_model=EngineStatusResponse)
async def read_status() -> EngineStatusResponse:
    return await asyncio.to_thread(_snapshot)


@router.post("/engine", response_model=EngineStatusResponse)
async def start_setup() -> EngineStatusResponse:
    """Start an install, or report the one already running.

    Never starts a second: two downloads writing the same cache paths is how a
    half-written font ends up passing an existence check.
    """
    global _install
    with _lock:
        if not _install.running:
            # Built complete, then published: a reader must never see an
            # _Install whose task hasn't been attached yet, which would report
            # "not running" and let a second POST start a rival download.
            fresh = _Install()
            fresh.task = asyncio.create_task(_run_install(fresh), name="engine-setup")
            _install = fresh
    return await asyncio.to_thread(_snapshot)
