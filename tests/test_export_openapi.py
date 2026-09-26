"""`api/export_openapi.py` produces the schema `openapi-typescript` consumes.

Two things this guards: that the stitched SSE routes actually carry the
`text/event-stream` union (not a leftover, un-typed `application/json` entry),
and that `python -m ...export_openapi` — the exact command issue #27 checked
in, and the one `.github/workflows/ci.yml` runs — actually works end to end in
a fresh interpreter.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from desktop_pdf_translator.api.export_openapi import _SSE_ROUTES, generate_schema

_SRC = Path(__file__).resolve().parents[1] / "src"


def test_schema_round_trips_through_json() -> None:
    schema = generate_schema()
    # generate_schema()/main() both feed this straight to json.dumps; a value
    # that doesn't round-trip (e.g. a stray Path or Enum member) would only
    # surface as a crash in CI, not a test failure, without this.
    reloaded = json.loads(json.dumps(schema))
    assert reloaded["openapi"].startswith("3.")


def test_non_sse_paths_are_unaffected() -> None:
    schema = generate_schema()
    health = schema["paths"]["/health"]["get"]
    assert health["responses"]["200"]["content"] == {
        "application/json": {"schema": {"$ref": "#/components/schemas/HealthResponse"}}
    }


def test_sse_routes_carry_only_the_stitched_event_stream_content() -> None:
    schema = generate_schema()
    for path, models in _SSE_ROUTES.items():
        operation = schema["paths"][path]["get"]
        content = operation["responses"]["200"]["content"]
        # No leftover `application/json` entry from FastAPI's un-response_model
        # default — the stitched text/event-stream schema must be the only one.
        assert list(content.keys()) == ["text/event-stream"]
        refs = content["text/event-stream"]["schema"]["oneOf"]
        assert refs == [
            {"$ref": f"#/components/schemas/{model.__name__}"} for model in models
        ]


def test_every_sse_model_lands_in_components_schemas() -> None:
    schema = generate_schema()
    component_schemas = schema["components"]["schemas"]
    for models in _SSE_ROUTES.values():
        for model in models:
            assert model.__name__ in component_schemas


def test_config_response_no_longer_erases_nested_settings() -> None:
    """The fix this issue required: ConfigResponse's translation/rag/gui/
    processing fields must be real models, or the generated frontend type for
    `GET /config` would be strictly worse than what it replaces. `translation`
    is `TranslationSettings` plus the `preferred_service` the frontend still
    reads (#85)."""
    schema = generate_schema()
    props = schema["components"]["schemas"]["ConfigResponse"]["properties"]
    assert props["translation"] == {"$ref": "#/components/schemas/TranslationConfig"}
    translation = schema["components"]["schemas"]["TranslationConfig"]["properties"]
    assert translation["model"]["$ref"] == "#/components/schemas/ModelRef"
    assert "preferred_service" in translation
    assert props["rag"] == {"$ref": "#/components/schemas/RAGSettings"}
    assert props["gui"] == {"$ref": "#/components/schemas/GUISettings"}
    assert props["processing"] == {"$ref": "#/components/schemas/ProcessingSettings"}


def test_cli_runs_in_a_fresh_interpreter() -> None:
    """Matches `test_sidecar_boot.py`'s subprocess style: this is a statement
    about a fresh process, and by the time pytest gets here `api.server` may
    already be imported by another test."""
    result = subprocess.run(
        [sys.executable, "-m", "desktop_pdf_translator.api.export_openapi"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(_SRC)},
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    schema = json.loads(result.stdout)
    assert schema["openapi"].startswith("3.")
