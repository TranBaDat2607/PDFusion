"""FastAPI sidecar entry point.

Run as a module:

    python -m desktop_pdf_translator.api.server

On startup it picks an ephemeral loopback port, generates a bearer token, and
prints a single line to stdout for the parent process (Tauri) to parse:

    READY port=<int> token=<urlsafe>

Any process that can read this stdout line can talk to the sidecar.
"""

from __future__ import annotations

import logging
import socket
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import get_settings
from .auth import init_token, require_token
from .routes import config as config_routes
from .routes import pdf as pdf_routes
from .routes import rag as rag_routes
from .routes import translation as translation_routes
from .schemas import HealthResponse

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    logger.info("Sidecar starting; loading settings…")
    get_settings()  # warm the singleton (loads .env, decrypts keys)
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
