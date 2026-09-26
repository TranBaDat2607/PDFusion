"""FastAPI sidecar entry point.

Run as a module:

    python -m desktop_pdf_translator.api.server

On startup it picks an ephemeral loopback port, generates a bearer token, and
prints a single line to stdout for the parent process (Tauri) to parse:

    READY port=<int> token=<urlsafe>

Any process that can read this stdout line can talk to the sidecar.

Everything on the path to that line has to stay cheap — the Tauri shell gives
up if it doesn't arrive. BabelDOC and the RAG stack are therefore imported
inside the handlers that need them, never by a route module or a package
`__init__`; `tests/test_sidecar_boot.py` fails if that regresses.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from uvicorn.main import STARTUP_FAILURE

from .. import __version__
from ..config import TranslationService, get_settings
from ..utils import appdata_dir, configure_logging
from .auth import init_token, require_token
from .routes import config as config_routes
from .routes import pdf as pdf_routes
from .routes import providers as providers_routes
from .routes import rag as rag_routes
from .routes import setup as setup_routes
from .routes import translation as translation_routes
from .schemas import HealthResponse

logger = logging.getLogger(__name__)


def _should_prewarm_argos(settings) -> bool:
    """Pre-warm Argos when it is what a translation would run: chosen, or in
    place of an LLM whose key is missing (`resolve_effective_service`).
    Otherwise skip, so LLM users don't pay the extra RAM for the CTranslate2
    model — a keyless local server (Ollama) included, which "no key anywhere"
    used to count as Argos's turn (#88).

    Gated on the pack already being available either way. Pre-warming is a
    background convenience; it must never be what starts an 80 MB download, at
    boot, with no UI attached to report it or fail it. Installing the pack is
    the setup flow's job (`api/routes/setup.py`), which the user can watch.
    """
    from ..engine_assets import argos_pack_ready

    if not argos_pack_ready():
        return False
    from ..translators.capabilities import resolve_effective_service

    running = resolve_effective_service(settings, settings.translation.model.provider)
    return running == TranslationService.ARGOS


def _prewarm_argos() -> None:
    """Best-effort warmup so the first user click doesn't pay cold-start.

    Materializes the language pack, applies our `argostranslate.settings`
    overrides, and forces the CTranslate2 Translator + tokenizer + sentencizer
    to load. Only ever reached when the pack is already on disk or bundled
    (`_should_prewarm_argos`), so `_ensure_en_vi_installed` here is a local
    install at worst, never a download.

    Implementation note: do NOT use `translate("warmup string")` — that path
    short-circuits on the SQLite cache, defeating the warmup entirely on the
    second run onwards. Call the low-level resolution directly.
    """
    try:
        from ..translators.argos_translator import (
            ArgosTranslator,
            _ensure_en_vi_installed,
        )

        logger.info("Argos pre-warm: starting (background)")
        # Pack install + settings overrides (logs "Argos CTranslate2 tuned: ...").
        _ensure_en_vi_installed()
        # Builds the ArgosTranslator instance and resolves the native
        # CTranslate2 handles, JIT-loading the int8 kernels and the
        # stanza-based sentencizer. This is the path real paragraphs hit.
        t = ArgosTranslator(lang_in="en", lang_out="vi")
        t._resolve_native_handles()
        logger.info("Argos pre-warm: done")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Argos pre-warm failed (non-fatal): %s", exc)


def _warm_translation_engine() -> None:
    """Import BabelDOC and load its layout model in the background so the
    first Translate click is warm.

    It costs 5-25 s — the wide end when the interpreter is cold and the
    `argos-prewarm` thread is contending for the GIL, which it now genuinely
    does: before the imports moved, that thread found everything already
    resolved. It used to be paid before READY, which is what put the handshake
    up against the Tauri shell's deadline. Paying it here keeps the engine ready
    without gating startup on it.

    The layout-model load is gated on `babeldoc_core_ready()` for the same
    reason `_should_prewarm_argos` gates on `argos_pack_ready()`:
    `DocLayoutModel.load_available()` *downloads* the ONNX model when it isn't
    cached, and this thread has no UI attached to report that or to fail it.
    Worse, `babeldoc.assets.download_file` writes in place with no temp +
    rename, so racing the setup flow's own download of the same path can leave
    a corrupt file that `verify_file` then unlinks. Installing is the setup
    flow's job (`api/routes/setup.py`), which the user can watch; this thread
    only ever warms what is already on disk.

    Importing BabelDOC is still unconditional — it touches no assets, and it is
    the larger half of the cold-start cost.

    A Translate click can still beat this thread, so the job path imports
    BabelDOC in a thread of its own rather than assuming this one won the race
    (`routes/translation.py:_load_engine`).
    """
    started = time.perf_counter()
    try:
        from ..engine_assets import babeldoc_core_ready
        from ..processors import processor  # noqa: F401

        if babeldoc_core_ready():
            from ..processors.doc_layout_cache import get_shared_doc_layout_model

            get_shared_doc_layout_model()
        else:
            logger.info(
                "Translation engine warm-up: layout model not installed yet, "
                "leaving it to the setup flow"
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Translation engine warm-up failed (non-fatal): %s", exc)
        return
    logger.info("Translation engine warm in %.1fs", time.perf_counter() - started)


def _sweep_orphan_translate_dirs(max_age_seconds: int = 3600) -> int:
    """Remove `pdfusion-translate-*` dirs left behind by a prior sidecar that
    crashed or was killed before its next-job cleanup could fire.

    Only sweeps dirs whose mtime is older than `max_age_seconds` (default 1h),
    so a sidecar restarting moments after the Tauri shell respawns it won't
    delete a still-active dir if two sidecars ever ran concurrently. Returns
    the count cleaned.
    """
    temp_root = Path(tempfile.gettempdir())
    cutoff = time.time() - max_age_seconds
    cleaned = 0
    try:
        candidates = list(temp_root.glob("pdfusion-translate-*"))
    except OSError as exc:
        logger.warning("Orphan sweep: could not enumerate %s (%s)", temp_root, exc)
        return 0
    for d in candidates:
        try:
            if not d.is_dir() or d.stat().st_mtime > cutoff:
                continue
            shutil.rmtree(d, ignore_errors=True)
            cleaned += 1
        except OSError as exc:
            logger.warning("Orphan sweep: could not remove %s (%s)", d, exc)
    return cleaned


def _gc_translation_cache() -> None:
    """Startup GC for the paragraph cache: reap expired rows, then enforce the
    size cap (which was previously declared but never enforced anywhere)."""
    try:
        from ..translators.translation_cache import get_translation_cache

        cache = get_translation_cache()
        expired = cache.clear_expired()
        evicted = cache.enforce_size_cap()
        if expired or evicted:
            logger.info(
                "Translation cache GC: %d expired, %d evicted for size",
                expired, evicted,
            )
    except Exception as exc:  # noqa: BLE001 — GC is best-effort
        logger.warning("Translation cache GC failed: %s", exc)


def _remove_legacy_vector_stores() -> None:
    """Delete the chat indexes older builds left under the data root (#59).

    `chroma_db/` is the chromadb 0.4 store, unreadable since the 1.x upgrade.
    `chroma_db_v2/` held every document's chunks in one shared collection. Chat
    indexes now live in `vectors/`, one collection per index recorded in
    `pdfusion.db`, and a document is indexed again the next time chat opens it.
    Filesystem work only — chromadb is never imported here, so the sweep costs
    nothing for the users who never chat.
    """
    root = appdata_dir()
    for name in ("chroma_db_v2", "chroma_db"):
        legacy = root / name
        if not legacy.is_dir():
            continue
        shutil.rmtree(legacy, ignore_errors=True)
        if legacy.exists():
            logger.warning("Could not fully remove the legacy vector store %s", legacy)
        else:
            logger.info("Removed the legacy vector store %s", legacy)


# Strong ref so the GC task isn't reaped mid-flight (create_task holds weak refs).
_startup_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    logger.info("Sidecar starting; loading settings…")
    cleaned = _sweep_orphan_translate_dirs()
    if cleaned:
        logger.info("Cleaned %d orphan translate temp dirs", cleaned)
    settings = get_settings()  # warm the singleton (loads .env, decrypts keys)
    threading.Thread(
        target=_warm_translation_engine,
        name="engine-warm",
        daemon=True,
    ).start()
    if _should_prewarm_argos(settings):
        threading.Thread(
            target=_prewarm_argos,
            name="argos-prewarm",
            daemon=True,
        ).start()
    # Paragraph-cache GC and the legacy vector-store sweep, in the background
    # so startup isn't delayed.
    for chore in (_gc_translation_cache, _remove_legacy_vector_stores):
        task = asyncio.create_task(asyncio.to_thread(chore))
        _startup_tasks.add(task)
        task.add_done_callback(_startup_tasks.discard)
    yield
    logger.info("Sidecar shutting down")


def _dev_origins_allowed() -> bool:
    """Whether the Vite dev server's origins belong in the allowlist.

    The Tauri shell answers this via `PDFUSION_DEV_ORIGINS` (`sidecar.rs:
    dev_origins_flag`), because it is the side that knows: it decides whether
    the webview loads from Vite or from the custom protocol.

    `sys.frozen` alone is *not* that answer, and relying on it was a bug.
    Frozen means "built by PyInstaller", not "shipped app": the Rust shell
    prefers a staged `binaries/pdfusion-sidecar-*.exe` over local Python
    whenever one is present, so after a build-sidecar run (a documented step
    before `pnpm tauri build`) `pnpm tauri dev` runs a *frozen* sidecar behind a
    *Vite-hosted* webview. Withholding the dev origins there rejects every
    request the app makes — CORS preflights come back `400 Disallowed CORS
    origin` — and it reads like the sidecar failed to start.

    `sys.frozen` stays as the fallback for a sidecar started without the shell:
    `python main.py` for backend debugging against `pnpm dev` gets the dev
    origins; a bundled exe run by hand does not.
    """
    signal = os.environ.get("PDFUSION_DEV_ORIGINS")
    if signal is not None:
        return signal == "1"
    return not getattr(sys, "frozen", False)


def _allowed_origins() -> list[str]:
    """Origins the webview can legitimately be running on.

    Previously `["*"]`, which let any web page the user happened to have open
    probe the loopback port and read `/health`'s response. Everything real
    still needs the bearer token, so this is hygiene rather than a hole — but
    the allowlist is short and known, so there's no reason to publish it.

    The production webview loads from Tauri's custom protocol, which WebView2
    presents as `http://tauri.localhost` (WebKit, on macOS/Linux, uses
    `tauri://localhost`). The Vite dev server's origins are added only in dev;
    see `_dev_origins_allowed` for how that's decided.
    """
    origins = [
        "http://tauri.localhost",
        "https://tauri.localhost",
        "tauri://localhost",
    ]
    if _dev_origins_allowed():
        origins += ["http://localhost:1420", "http://127.0.0.1:1420"]
    return origins


def create_app() -> FastAPI:
    app = FastAPI(
        title="PDFusion sidecar",
        version=__version__,
        lifespan=_lifespan,
    )

    # The Tauri webview talks to http://127.0.0.1:<port>.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins(),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:  # noqa: D401 — endpoint
        # `AppSettings.version` was removed in #25: `ConfigManager` persisted
        # the whole model, so a stored value would have shadowed the model
        # default forever, freezing every upgraded install's `/health` at
        # whatever build first wrote its config.toml. `__version__` is the
        # single source of truth now, and `create_app()`'s FastAPI `version=`
        # reads the same constant.
        return HealthResponse(version=__version__)

    # Authenticated routes
    app.include_router(config_routes.router)
    app.include_router(providers_routes.router)
    app.include_router(translation_routes.router)
    app.include_router(rag_routes.router)
    app.include_router(setup_routes.router)
    app.include_router(pdf_routes.router)

    # Authenticated catch-all health (so Tauri's `wait_for_health` can also
    # confirm the bearer token is correct, not just that the process is alive).
    @app.get("/auth/ping", dependencies=[Depends(require_token)])
    async def auth_ping() -> dict:
        return {"ok": True}

    return app


def _bind_socket() -> tuple[socket.socket, int]:
    """Claim an ephemeral loopback port, start listening, and keep holding it.

    The socket is handed straight to uvicorn, so nothing else can take the port
    between the announcement and the server coming up. Picking a port by
    binding, closing and letting uvicorn re-bind left exactly that window open.

    `listen()` here rather than leaving it to uvicorn, because READY is printed
    before `create_app()` and the lifespan run: a client that connects in that
    gap lands in the backlog and is served a moment later, instead of taking a
    connection refused it would have to know to retry. `asyncio`'s
    `create_server(sock=...)` calls `listen()` again when uvicorn starts, which
    only resets the backlog on an already-listening socket.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    return sock, sock.getsockname()[1]


def main() -> None:
    # Shared with main.py's entry point (see utils/logging_setup.py) — this is
    # what makes `python -m desktop_pdf_translator.api.server` (the dev path
    # `pnpm tauri dev` actually spawns) and the `pdfusion-sidecar` console
    # script (pyproject.toml) get the same rotating app.log that the bundled
    # exe already got via main.py. See #26.
    configure_logging()

    token = init_token()
    sock, port = _bind_socket()

    # Single-line handshake for the parent process. Flushed immediately so
    # Tauri can read it before any other output.
    print(f"READY port={port} token={token}", flush=True)

    config = uvicorn.Config(
        create_app(),
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.run(sockets=[sock])

    # `uvicorn.run()` ends with this; `Server.run()` does not. A lifespan that
    # raises — a corrupt config.toml making get_settings() throw, say — sets
    # should_exit and returns normally, so without the check the process reports
    # success on a startup failure. READY has already been printed by then, so
    # the shell would otherwise only find out by waiting out its whole health
    # timeout and blaming /auth/ping.
    if not server.started:
        sys.exit(STARTUP_FAILURE)


if __name__ == "__main__":
    main()
