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

from .. import __version__
from ..config import TranslationService, get_settings
from .auth import init_token, require_token
from .routes import config as config_routes
from .routes import pdf as pdf_routes
from .routes import rag as rag_routes
from .routes import translation as translation_routes
from .schemas import HealthResponse

logger = logging.getLogger(__name__)


def _should_prewarm_argos(settings) -> bool:
    """Pre-warm Argos when it's the active default or the only usable backend.

    - Preferred service is Argos → yes.
    - No LLM API key configured anywhere → Argos is the inevitable fallback.
    Otherwise skip so LLM-only users don't pay the ~80MB pack download or the
    extra RAM for the CTranslate2 model.
    """
    if settings.translation.preferred_service == TranslationService.ARGOS:
        return True
    any_llm_key = any(
        settings.has_api_key(s)
        for s in (
            TranslationService.OPENAI,
            TranslationService.GEMINI,
            TranslationService.ANTHROPIC,
        )
    )
    return not any_llm_key


def _prewarm_argos() -> None:
    """Best-effort warmup so the first user click doesn't pay cold-start.

    Materializes the language pack (downloads ~80 MB if first run), applies
    our `argostranslate.settings` overrides, and forces the CTranslate2
    Translator + tokenizer + sentencizer to load.

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
    """Import BabelDOC in the background so the first Translate click is warm.

    It costs ~5 s, and it used to be paid before READY — which is what put the
    handshake up against the Tauri shell's deadline. Paying it here keeps the
    engine ready without gating startup on it.
    """
    started = time.perf_counter()
    try:
        from ..processors import processor  # noqa: F401
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
    # Paragraph-cache GC in the background so startup isn't delayed.
    gc_task = asyncio.create_task(asyncio.to_thread(_gc_translation_cache))
    _startup_tasks.add(gc_task)
    gc_task.add_done_callback(_startup_tasks.discard)
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
    whenever one is present, so after `build-sidecar.ps1` (a documented step
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
    settings = get_settings()
    app = FastAPI(
        title="PDFusion sidecar",
        version=settings.version,
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
        # The package constant, not `settings.version`. `ConfigManager` persists
        # the whole model (`settings.dict()`), so every config.toml already on
        # disk carries the version that shipped with it — and a stored value
        # shadows the model default forever. Reporting it would mean /health
        # announcing 1.0.0 from a 1.0.6 build on every upgraded install.
        return HealthResponse(version=__version__)

    # Authenticated routes
    app.include_router(config_routes.router)
    app.include_router(translation_routes.router)
    app.include_router(rag_routes.router)
    app.include_router(pdf_routes.router)

    # Authenticated catch-all health (so Tauri's `wait_for_health` can also
    # confirm the bearer token is correct, not just that the process is alive).
    @app.get("/auth/ping", dependencies=[Depends(require_token)])
    async def auth_ping() -> dict:
        return {"ok": True}

    return app


def _bind_socket() -> tuple[socket.socket, int]:
    """Claim an ephemeral loopback port and keep holding it.

    The socket is handed straight to uvicorn, so nothing else can take the port
    between the announcement and the server coming up. Picking a port by
    binding, closing and letting uvicorn re-bind left exactly that window open.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    return sock, sock.getsockname()[1]


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

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
    uvicorn.Server(config).run(sockets=[sock])


if __name__ == "__main__":
    main()
