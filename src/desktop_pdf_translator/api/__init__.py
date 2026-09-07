"""FastAPI sidecar exposing PDFusion's Python backend over loopback HTTP.

Nothing is re-exported here: importing any `api.*` submodule runs this file, so
a `from .server import create_app` would drag BabelDOC and torch into every
consumer — including the tests — before the sidecar can print READY.
"""
