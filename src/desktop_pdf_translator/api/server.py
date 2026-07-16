"""FastAPI sidecar entry point.

Run as a module:

    python -m desktop_pdf_translator.api.server

On startup it picks an ephemeral loopback port, generates a bearer token, and
prints a single line to stdout for the parent process (Tauri) to parse:

    READY port=<int> token=<urlsafe>

Any process that can read this stdout line can talk to the sidecar.
"""

from __future__ import annotations

import asyncio
import logging
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


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="PDFusion sidecar",
        version=settings.version,
        lifespan=_lifespan,
    )

    # The Tauri webview talks to http://127.0.0.1:<port>. We allow any local
    # origin so dev mode (vite on 1420) and the production webview both work.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:  # noqa: D401 — endpoint
        return HealthResponse(version=settings.version)

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


def _pick_port() -> int:
    """Reserve an ephemeral loopback port. Small race window between close()
    and uvicorn binding, but acceptable on Windows for localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    token = init_token()
    port = _pick_port()

    # Single-line handshake for the parent process. Flushed immediately so
    # Tauri can read it before any other output.
    print(f"READY port={port} token={token}", flush=True)

    app = create_app()
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
