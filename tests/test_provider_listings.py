"""Every provider that speaks OpenAI's API at its own URL, listed from a
recorded response (#88).

Adding such a provider is meant to be a registry entry plus
`fixtures/model_lists/<id>.json`, in the provider's documented `GET /models`
shape — no test code. This file is what reads that fixture: it drives the
entry's own lister at the entry's own endpoint (`registry.endpoint_for`, as
`catalog.list_models` does) through `httpx.MockTransport`, so nothing leaves
the machine, and checks the request went to that provider rather than to
OpenAI, and that OpenAI's non-chat filter kept its hands off another
provider's ids.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List
from urllib.parse import urlsplit

import httpx
import pytest

from desktop_pdf_translator.providers.registry import (
    PROVIDERS,
    endpoint_for,
    request_key,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "model_lists"

BORROWING_OPENAI = [spec for spec in PROVIDERS if spec.protocol == "openai" and spec.id != "openai"]


@pytest.fixture(autouse=True)
def _no_sdk_env_overrides(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)


@pytest.mark.parametrize("spec", BORROWING_OPENAI, ids=lambda spec: spec.id)
def test_a_provider_speaking_openais_api_lists_its_own_models_from_its_own_url(spec):
    fixture = _FIXTURES / f"{spec.id}.json"
    assert fixture.exists(), f"record {spec.id}'s GET /models response in {fixture.name}"
    body = json.loads(fixture.read_text(encoding="utf-8"))
    expected = [entry["id"] for entry in body["data"]]
    endpoint = endpoint_for(spec, None)
    own = urlsplit(endpoint)
    seen: List[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == own.hostname and request.url.path == f"{own.path}/models":
            return httpx.Response(200, json=body)
        return httpx.Response(404, json={"error": {"message": f"unexpected {request.url}"}})

    client = httpx.Client(transport=httpx.MockTransport(answer))
    key = request_key(spec, "sk-recorded" if spec.requires_key else None)

    listing = spec.lister()(key, endpoint, http_client=client)

    assert [model.id for model in listing.models] == expected
    assert listing.hidden == ()
    assert len(seen) == 1
    assert seen[0].url.host == own.hostname
    assert seen[0].headers["authorization"] == f"Bearer {key}"
