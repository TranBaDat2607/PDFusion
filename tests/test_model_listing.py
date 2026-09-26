"""Listing a provider's models from its free list endpoint (#84).

A key is verified and the model picker filled by asking the provider which
models the key can use — never by generating text, which costs money and
fails for reasons that have nothing to do with the key. Each protocol gets one
lister; these tests drive them against recorded responses through
`httpx.MockTransport`, so nothing leaves the machine.

The fixtures under `fixtures/model_lists/` are hand-built in each provider's
documented response shape. Every expected value below is read from those files
and the spec in #84, not from a lister's output.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, List

import anthropic
import httpx
import openai
import pytest
from google.genai import errors as genai_errors

from desktop_pdf_translator.providers.listing import (
    ListedModel,
    Listing,
    list_anthropic_models,
    list_gemini_models,
    list_openai_models,
    model_matches,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "model_lists"

OPENAI_CHAT_IDS = ["gpt-4.1", "gpt-4o-mini", "o3", "chatgpt-4o-latest", "gpt-5"]
# One id per pattern in #84's table, so a pattern the filter forgets shows up
# as a chat model the user can pick and then can't translate with.
OPENAI_NON_CHAT_IDS = {
    "text-embedding-3-small",  # text-embedding-*
    "tts-1-hd",  # tts-*
    "whisper-1",  # whisper-*
    "dall-e-3",  # dall-e-*
    "gpt-image-1",  # gpt-image-*
    "omni-moderation-latest",  # *moderation*
    "gpt-4o-realtime-preview",  # *-realtime*
    "gpt-4o-audio-preview",  # *-audio*
    "gpt-4o-mini-transcribe",  # *-transcribe*
    "davinci-002",  # legacy davinci
    "babbage-002",  # legacy babbage
}

ANTHROPIC_PAGE1_LAST_ID = "claude-haiku-4-5-20251001"
GEMINI_PAGE2_TOKEN = "page-2-token"


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _mock_client(
    handler: Callable[[httpx.Request], httpx.Response], seen: List[httpx.Request]
) -> httpx.Client:
    """An `httpx.Client` that answers from `handler` and records each request.

    Routing mistakes answer 404 rather than raising inside the transport: the
    SDKs wrap a transport exception as a connection error (after retrying it),
    which would hide what actually went wrong. The tests assert on `seen`.
    """

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(record))


def _not_found(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        404, json={"error": {"message": f"unexpected request {request.url}"}}
    )


@pytest.fixture(autouse=True)
def _no_sdk_env_overrides(monkeypatch: pytest.MonkeyPatch):
    """The SDKs read a base URL (and Vertex mode) from the environment when
    none is passed; a developer's shell must not redirect the default-endpoint
    tests."""
    for name in (
        "OPENAI_BASE_URL",
        "ANTHROPIC_BASE_URL",
        "GOOGLE_GEMINI_BASE_URL",
        "GOOGLE_GENAI_USE_VERTEXAI",
    ):
        monkeypatch.delenv(name, raising=False)


# --- OpenAI -----------------------------------------------------------------


def _openai_handler(body: dict, host: str, path: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.host == host and request.url.path == path:
            return httpx.Response(200, json=body)
        return _not_found(request)

    return handler


def test_openai_hides_non_chat_models_on_its_own_endpoint():
    """The id filter is a heuristic, so what it removes goes to `hidden` for a
    "show all" toggle instead of being thrown away."""
    seen: List[httpx.Request] = []
    client = _mock_client(
        _openai_handler(_fixture("openai.json"), "api.openai.com", "/v1/models"), seen
    )

    listing = list_openai_models("sk-test-key", None, http_client=client)

    assert isinstance(listing, Listing)
    assert [m.id for m in listing.models] == OPENAI_CHAT_IDS
    hidden_ids = [m.id for m in listing.hidden]
    assert set(hidden_ids) == OPENAI_NON_CHAT_IDS
    assert len(hidden_ids) == len(OPENAI_NON_CHAT_IDS)
    assert len(seen) == 1
    assert seen[0].headers["authorization"] == "Bearer sk-test-key"


def test_openai_hides_non_chat_models_at_its_own_url_named_explicitly():
    """A caller that resolves the endpoint before listing (`registry.
    endpoint_for`, #88) passes OpenAI's own URL explicitly rather than
    `None`; the filter must still apply — treating any non-`None` `base_url`
    as a custom server's would offer `whisper-1` and the embedding models as
    if OpenAI itself were an OpenAI-compatible local server."""
    seen: List[httpx.Request] = []
    client = _mock_client(
        _openai_handler(_fixture("openai.json"), "api.openai.com", "/v1/models"), seen
    )

    listing = list_openai_models(
        "sk-test-key", "https://api.openai.com/v1", http_client=client
    )

    assert [m.id for m in listing.models] == OPENAI_CHAT_IDS
    assert set(m.id for m in listing.hidden) == OPENAI_NON_CHAT_IDS


def test_openai_hides_non_chat_models_at_its_own_url_with_a_trailing_slash():
    seen: List[httpx.Request] = []
    client = _mock_client(
        _openai_handler(_fixture("openai.json"), "api.openai.com", "/v1/models"), seen
    )

    listing = list_openai_models(
        "sk-test-key", "https://api.openai.com/v1/", http_client=client
    )

    assert set(m.id for m in listing.hidden) == OPENAI_NON_CHAT_IDS


def test_openai_hides_nothing_at_another_providers_own_endpoint():
    """OpenRouter speaks OpenAI's API at its own URL (#88); its ids follow no
    convention the filter's patterns assume, so nothing is hidden there
    either — as at a local server's endpoint."""
    seen: List[httpx.Request] = []
    client = _mock_client(
        _openai_handler(_fixture("openai.json"), "openrouter.ai", "/api/v1/models"), seen
    )

    listing = list_openai_models(
        "sk-or-test", "https://openrouter.ai/api/v1", http_client=client
    )

    assert [m.id for m in listing.models] == [
        m["id"] for m in _fixture("openai.json")["data"]
    ]
    assert listing.hidden == ()


def test_openai_custom_endpoint_hides_nothing():
    """Ollama / LM Studio name models freely — `nomic-embed-text` or a
    `whisper-*` there may be exactly what the user loaded — so a custom
    endpoint gets no filter at all, and the request goes to that host."""
    seen: List[httpx.Request] = []
    client = _mock_client(
        _openai_handler(_fixture("openai_custom.json"), "localhost", "/v1/models"), seen
    )

    listing = list_openai_models(
        "ollama", "http://localhost:11434/v1", http_client=client
    )

    assert [m.id for m in listing.models] == [
        "llama3.2:latest",
        "nomic-embed-text:latest",
        "text-embedding-3-small",
        "whisper-large-v3",
        "qwen2.5-audio",
    ]
    assert listing.hidden == ()
    assert len(seen) == 1
    assert seen[0].url.host == "localhost"
    assert seen[0].url.port == 11434
    assert seen[0].url.path == "/v1/models"
    assert seen[0].headers["authorization"] == "Bearer ollama"


def test_openai_created_seconds_become_milliseconds():
    """Every timestamp the stores keep is Unix milliseconds; OpenAI's `created`
    is seconds. It carries no display name or token limits. Hidden models are
    mapped the same way, since "show all" puts them in the same picker."""
    seen: List[httpx.Request] = []
    client = _mock_client(
        _openai_handler(_fixture("openai.json"), "api.openai.com", "/v1/models"), seen
    )

    listing = list_openai_models("sk-test-key", None, http_client=client)

    assert listing.models[0] == ListedModel(id="gpt-4.1", created_at=1744316542000)
    assert ListedModel(id="whisper-1", created_at=1677532384000) in listing.hidden


def _openai_list_of(ids: List[str]) -> Callable[[httpx.Request], httpx.Response]:
    body = {
        "object": "list",
        "data": [{"id": i, "object": "model", "created": 1, "owned_by": "openai"} for i in ids],
    }
    return lambda request: httpx.Response(200, json=body)


# Served by OpenAI but not by `chat.completions`, the only call the translator
# makes: completions-only, Responses-only, speech and video models whose ids the
# issue's prefix patterns miss.
OPENAI_UNCALLABLE_IDS = [
    "gpt-3.5-turbo-instruct",
    "o1-pro",
    "o3-pro",
    "codex-mini-latest",
    "gpt-5-codex",
    "computer-use-preview",
    "o3-deep-research",
    "o4-mini-deep-research",
    "gpt-4o-mini-tts",
    "sora-2",
    "chatgpt-image-latest",
]

# Chat models whose ids contain one of those words, which must stay on offer.
OPENAI_CHAT_LOOKALIKES = [
    "gpt-4o-search-preview",
    "gpt-4.1-nano",
    "o4-mini",
    "gpt-5-chat-latest",
]


def test_openai_hides_models_chat_completions_cannot_call():
    """The picker saves a listed model without a check, so one the translator
    can't call would fail every paragraph with a 404 — which isn't fatal, so
    the job runs to the end and hands back the source text."""
    seen: List[httpx.Request] = []
    client = _mock_client(
        _openai_list_of(OPENAI_UNCALLABLE_IDS + OPENAI_CHAT_LOOKALIKES), seen
    )

    listing = list_openai_models("sk-test-key", None, http_client=client)

    assert [m.id for m in listing.models] == OPENAI_CHAT_LOOKALIKES
    assert [m.id for m in listing.hidden] == OPENAI_UNCALLABLE_IDS


# --- Anthropic --------------------------------------------------------------


def _anthropic_handler(host: str, path: str):
    page1, page2 = _fixture("anthropic_page1.json"), _fixture("anthropic_page2.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "GET" or request.url.host != host or request.url.path != path:
            return _not_found(request)
        after_id = request.url.params.get("after_id")
        if after_id is None:
            return httpx.Response(200, json=page1)
        if after_id == ANTHROPIC_PAGE1_LAST_ID:
            return httpx.Response(200, json=page2)
        return _not_found(request)

    return handler


def test_anthropic_follows_pagination():
    """The old code asked for 100 models and never fetched the next page, so a
    model past the first page could never be picked."""
    seen: List[httpx.Request] = []
    client = _mock_client(_anthropic_handler("api.anthropic.com", "/v1/models"), seen)

    listing = list_anthropic_models("sk-ant-test", None, http_client=client)

    assert [m.id for m in listing.models] == [
        "claude-opus-4-1-20250805",
        "claude-haiku-4-5-20251001",
        "claude-3-haiku-20240307",
    ]
    assert listing.hidden == ()
    assert len(seen) == 2
    assert "after_id" not in seen[0].url.params
    assert seen[1].url.params["after_id"] == ANTHROPIC_PAGE1_LAST_ID
    assert all(r.headers["x-api-key"] == "sk-ant-test" for r in seen)


def test_anthropic_stops_when_a_page_does_not_advance():
    """A proxy that answers every page with the same `last_id` and
    `has_more: true` must not keep a worker thread paging forever — the
    route's deadline gives up on the result, not on the thread."""
    page1 = _fixture("anthropic_page1.json")
    seen: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # A bound, so the failure is a 404 here rather than a hung run.
        return httpx.Response(200, json=page1) if len(seen) <= 5 else _not_found(request)

    client = _mock_client(handler, seen)

    listing = list_anthropic_models("sk-ant-test", None, http_client=client)

    assert len(seen) == 2
    assert [m.id for m in listing.models][: len(page1["data"])] == [
        m["id"] for m in page1["data"]
    ]


def test_anthropic_maps_display_name_timestamp_and_limits():
    """`created_at` is RFC 3339 on the wire and milliseconds in the stores;
    older responses carry no token limits, which must read as unknown rather
    than as zero."""
    client = _mock_client(_anthropic_handler("api.anthropic.com", "/v1/models"), [])

    listing = list_anthropic_models("sk-ant-test", None, http_client=client)

    assert listing.models == (
        ListedModel(
            id="claude-opus-4-1-20250805",
            display_name="Claude Opus 4.1",
            context_tokens=200000,
            output_tokens=32000,
            created_at=1754352000000,  # 2025-08-05T00:00:00Z
        ),
        ListedModel(
            id="claude-haiku-4-5-20251001",
            display_name="Claude Haiku 4.5",
            context_tokens=200000,
            output_tokens=64000,
            created_at=1760531400000,  # 2025-10-15T12:30:00Z
        ),
        ListedModel(
            id="claude-3-haiku-20240307",
            display_name="Claude Haiku 3",
            context_tokens=None,
            output_tokens=None,
            created_at=1709769600000,  # 2024-03-07T00:00:00Z
        ),
    )


def test_anthropic_custom_base_url_is_where_the_request_goes():
    """A saved key is only ever sent to the endpoint it was saved for."""
    seen: List[httpx.Request] = []
    client = _mock_client(_anthropic_handler("proxy.example.com", "/v1/models"), seen)

    listing = list_anthropic_models(
        "sk-ant-test", "https://proxy.example.com", http_client=client
    )

    assert len(listing.models) == 3
    assert seen and all(r.url.host == "proxy.example.com" for r in seen)


# --- Gemini -----------------------------------------------------------------


def _gemini_handler(api_key: str):
    page1, page2 = _fixture("gemini_page1.json"), _fixture("gemini_page2.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if (
            request.method != "GET"
            or request.url.host != "generativelanguage.googleapis.com"
            or not request.url.path.endswith("/models")
        ):
            return _not_found(request)
        sent_key = request.headers.get("x-goog-api-key") or request.url.params.get("key")
        if sent_key != api_key:
            return httpx.Response(
                401, json={"error": {"code": 401, "status": "UNAUTHENTICATED"}}
            )
        token = request.url.params.get("pageToken")
        if token is None:
            return httpx.Response(200, json=page1)
        if token == GEMINI_PAGE2_TOKEN:
            return httpx.Response(200, json=page2)
        return _not_found(request)

    return handler


def test_gemini_keeps_generate_content_models_across_pages():
    """`generateContent` support is a capability fact, not a guess, so the
    embedding and Imagen models are dropped outright — not offered under "show
    all". The wire id's `models/` prefix is not part of the model name the
    translator is configured with."""
    seen: List[httpx.Request] = []
    client = _mock_client(_gemini_handler("gm-test-key"), seen)

    listing = list_gemini_models("gm-test-key", http_client=client)

    assert listing.models == (
        ListedModel(
            id="gemini-2.5-flash",
            display_name="Gemini 2.5 Flash",
            context_tokens=1048576,
            output_tokens=65536,
        ),
        ListedModel(
            id="gemini-2.0-flash-lite",
            display_name="Gemini 2.0 Flash-Lite",
            context_tokens=1048576,
            output_tokens=8192,
        ),
        ListedModel(
            id="gemini-2.5-pro",
            display_name="Gemini 2.5 Pro",
            context_tokens=1048576,
            output_tokens=65536,
        ),
    )
    assert listing.hidden == ()
    assert len(seen) == 2
    assert "pageToken" not in seen[0].url.params
    assert seen[1].url.params["pageToken"] == GEMINI_PAGE2_TOKEN


def test_gemini_hides_generate_content_models_that_do_not_answer_in_text():
    """Gemini's speech, image and computer-use variants all advertise
    `generateContent`, but a translation needs text back."""
    text = ["models/gemini-2.5-flash", "models/gemini-2.5-pro", "models/gemma-3-27b-it"]
    other = [
        "models/gemini-2.5-flash-preview-tts",
        "models/gemini-2.5-pro-preview-tts",
        "models/gemini-2.5-flash-image",
        "models/gemini-2.0-flash-preview-image-generation",
        "models/gemini-2.5-flash-native-audio-preview-09-2025",
        "models/gemini-2.5-computer-use-preview-10-2025",
    ]
    body = {
        "models": [
            {"name": name, "supportedGenerationMethods": ["generateContent", "countTokens"]}
            for name in text + other
        ]
    }
    seen: List[httpx.Request] = []
    client = _mock_client(lambda request: httpx.Response(200, json=body), seen)

    listing = list_gemini_models("gm-test-key", http_client=client)

    assert [m.id for m in listing.models] == [n.removeprefix("models/") for n in text]
    assert [m.id for m in listing.hidden] == [n.removeprefix("models/") for n in other]


# --- A refused key ----------------------------------------------------------


def _unauthorized(body: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json=body)

    return handler


@pytest.mark.parametrize(
    ("lister", "body", "error_type", "status_of"),
    [
        (
            list_openai_models,
            {
                "error": {
                    "message": "Incorrect API key provided",
                    "type": "invalid_request_error",
                    "code": "invalid_api_key",
                }
            },
            openai.AuthenticationError,
            lambda exc: exc.status_code,
        ),
        (
            list_anthropic_models,
            {
                "type": "error",
                "error": {"type": "authentication_error", "message": "invalid x-api-key"},
            },
            anthropic.AuthenticationError,
            lambda exc: exc.status_code,
        ),
        (
            list_gemini_models,
            {
                "error": {
                    "code": 401,
                    "message": "Request had invalid authentication credentials.",
                    "status": "UNAUTHENTICATED",
                }
            },
            genai_errors.ClientError,
            lambda exc: exc.code,
        ),
    ],
    ids=["openai", "anthropic", "gemini"],
)
def test_a_refused_key_propagates_with_its_status(lister, body, error_type, status_of):
    """The caller tells "key refused" from "endpoint down" by the status, so a
    lister must not swallow the error or turn it into an empty listing."""
    client = _mock_client(_unauthorized(body), [])

    with pytest.raises(error_type) as caught:
        lister("bad-key", None, http_client=client)

    assert status_of(caught.value) == 401


# --- model_matches ----------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "ids", "expected"),
    [
        ("gpt-4.1-mini", ["gpt-4.1-mini"], True),
        # An alias resolves to a dated id...
        ("claude-haiku-4-5", ["claude-haiku-4-5-20251001"], True),
        # ...and an Ollama name to its tagged one.
        ("llama3.2", ["llama3.2:latest"], True),
        ("gpt-4", ["gpt-4-turbo"], True),
        ("gpt-4o", ["o3", "gpt-4o-mini"], True),
        # A typo is what this exists to catch.
        ("gpt-4.1-mni", ["gpt-4.1-mini"], False),
        # A prefix only counts at a `-` / `:` boundary.
        ("gpt-4", ["gpt-40"], False),
        ("claude-haiku", ["claude-haiku4"], False),
        ("llama3", ["llama3.2:latest"], False),
        # The saved name must not be longer than what the endpoint offers.
        ("gpt-4.1-mini", ["gpt-4.1"], False),
        ("gpt-4o", [], False),
    ],
)
def test_model_matches(model, ids, expected):
    assert model_matches(model, ids) is expected


def test_model_matches_accepts_any_iterable():
    """Callers pass a generator over a listing's models, not only a list."""
    listed = (ListedModel(id=i) for i in ["o3", "gpt-4-turbo"])

    assert model_matches("gpt-4", (m.id for m in listed)) is True
