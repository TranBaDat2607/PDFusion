"""Bearer-token authentication for the sidecar."""

import os
import secrets
from fastapi import Header, HTTPException, status

_TOKEN: str | None = None


def init_token() -> str:
    """Generate (or reuse) the sidecar's bearer token. Called once on startup."""
    global _TOKEN
    if _TOKEN is None:
        _TOKEN = os.environ.get("PDFUSION_API_TOKEN") or secrets.token_urlsafe(32)
    return _TOKEN


def get_token() -> str:
    if _TOKEN is None:
        raise RuntimeError("Auth token not initialized; call init_token() first")
    return _TOKEN


async def require_token(authorization: str | None = Header(default=None)) -> None:
    """FastAPI dependency that validates the Authorization: Bearer <token> header."""
    expected = get_token()
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
        )
    presented = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
        )
