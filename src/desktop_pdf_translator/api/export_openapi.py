"""Emit the sidecar's OpenAPI schema, with SSE event payloads stitched in.

FastAPI's own `app.openapi()` has no idea what `EventSourceResponse` sends —
every SSE route is a bare `-> EventSourceResponse` with no `response_model`,
so those three routes' schemas would otherwise be an ungenerated placeholder
(`"application/json": {"schema": {}}`). This script builds the same schema
`create_app().openapi()` produces and rewrites those routes' 200 responses to
describe the real `text/event-stream` union, using the models in
`api/sse_schemas.py`. It never touches `server.py` or any route module —
`create_app()` and the real running sidecar behave identically either way.

Run as a module:

    python -m desktop_pdf_translator.api.export_openapi --output desktop/src/lib/openapi.json

Always pass --output for scripted/CI use: it opens the file itself with an
explicit `encoding="utf-8"`, independent of whatever a calling shell's `>`
redirection would default to. Output is plain `json.dumps(..., indent=2,
sort_keys=True)` with the default `ensure_ascii=True` — several `Field`
descriptions and docstrings in this codebase use non-ASCII punctuation (em
dashes, arrows), and printing those without `ensure_ascii`'s `\\uXXXX`
escaping crashes with `UnicodeEncodeError` on a non-UTF-8 console. Escaping
keeps the output pure ASCII, so this is safe on any console codepage, on any
OS — no `sys.stdout.reconfigure` needed.

This is a standalone script: it is never imported by `server.py`, a route
module, or any package `__init__.py`, so `tests/test_sidecar_boot.py`'s
boot-cost invariant does not apply to it, and it is free to import
`create_app` eagerly at module scope.
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List, Type

from fastapi.openapi.constants import REF_TEMPLATE
from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from .server import create_app
from .sse_schemas import (
    AskProgressPayload,
    AskResultPayload,
    CancelPayload,
    ChunkReadyEventPayload,
    CompletionEventPayload,
    EmptyPayload,
    IndexDonePayload,
    IndexProgressPayload,
    JobErrorPayload,
    ParagraphTranslatedEventPayload,
    ProgressEventPayload,
)

# OpenAPI path -> ordered list of models that can appear on that stream.
# Order only affects the `oneOf` list's readability, not behavior.
_SSE_ROUTES: Dict[str, List[Type[BaseModel]]] = {
    "/translate/{job_id}/events": [
        ProgressEventPayload,
        ChunkReadyEventPayload,
        ParagraphTranslatedEventPayload,
        CompletionEventPayload,
        JobErrorPayload,
        CancelPayload,
    ],
    "/rag/index/{job_id}/events": [
        IndexProgressPayload,
        IndexDonePayload,
        JobErrorPayload,
        EmptyPayload,  # cancelled
    ],
    "/rag/ask/{job_id}/events": [
        AskProgressPayload,
        AskResultPayload,
        JobErrorPayload,
        EmptyPayload,  # cancelled
    ],
}


def _sse_component_schemas() -> Dict[str, Any]:
    """JSON Schema `$defs` for every model referenced from `_SSE_ROUTES`,
    named/deduplicated by `models_json_schema` and ready to merge into
    `components.schemas`."""
    seen: Dict[Type[BaseModel], None] = {}
    for models in _SSE_ROUTES.values():
        for model in models:
            seen.setdefault(model, None)
    # Returns (key_map, json_schema); the definitions always live under
    # json_schema["$defs"] regardless of ref_template — only the internal
    # $ref strings follow it. REF_TEMPLATE is the exact constant FastAPI's
    # own get_openapi() uses, so these refs match the rest of the document.
    _, json_schema = models_json_schema(
        [(model, "validation") for model in seen], ref_template=REF_TEMPLATE
    )
    return json_schema["$defs"]


def generate_schema() -> Dict[str, Any]:
    """The sidecar's OpenAPI schema, with SSE payload shapes stitched in."""
    schema = create_app().openapi()
    schema["components"].setdefault("schemas", {})
    schema["components"]["schemas"].update(_sse_component_schemas())

    for path, models in _SSE_ROUTES.items():
        operation = schema["paths"][path]["get"]
        # Replace the whole `content` dict: FastAPI defaults an
        # un-response_model'd route to a bogus `application/json` entry,
        # which must not survive alongside the real `text/event-stream` one.
        operation["responses"]["200"]["content"] = {
            "text/event-stream": {
                "schema": {
                    "oneOf": [
                        {"$ref": f"#/components/schemas/{model.__name__}"}
                        for model in models
                    ]
                }
            }
        }
    return schema


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o",
        "--output",
        help="Write the schema here instead of stdout (recommended for scripted/CI use).",
    )
    args = parser.parse_args()

    text = json.dumps(generate_schema(), indent=2, sort_keys=True)

    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.write("\n")
    else:
        print(text)


if __name__ == "__main__":
    main()
