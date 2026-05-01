"""FastAPI sidecar exposing PDFusion's Python backend over loopback HTTP."""

from .server import create_app

__all__ = ["create_app"]
