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

# Offered on OpenAI's own endpoint: chat models released in 2025 or later. The
# two 2024 chat models in the fixture (gpt-4o-mini, chatgpt-4o-latest) move to
# "show all" along with the non-chat ids.
OPENAI_CHAT_IDS = ["gpt-4.1", "o3", "gpt-5"]
OPENAI_OLD_CHAT_IDS = {"gpt-4o-mini", "chatgpt-4o-latest"}
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

# Models released before this instant are no longer offered by default.
CUTOFF_S = 1735689600  # 2025-01-01T00:00:00Z


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
    assert set(hidden_ids) == OPENAI_NON_CHAT_IDS | OPENAI_OLD_CHAT_IDS
    assert len(hidden_ids) == len(OPENAI_NON_CHAT_IDS | OPENAI_OLD_CHAT_IDS)
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
    assert set(m.id for m in listing.hidden) == (
        OPENAI_NON_CHAT_IDS | OPENAI_OLD_CHAT_IDS
    )


def test_openai_hides_non_chat_models_at_its_own_url_with_a_trailing_slash():
    seen: List[httpx.Request] = []
    client = _mock_client(
        _openai_handler(_fixture("openai.json"), "api.openai.com", "/v1/models"), seen
    )

    listing = list_openai_models(
        "sk-test-key", "https://api.openai.com/v1/", http_client=client
    )

    assert set(m.id for m in listing.hidden) == (
        OPENAI_NON_CHAT_IDS | OPENAI_OLD_CHAT_IDS
    )


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
    """A compatible server names models freely — `nomic-embed-text` or a
    `whisper-*` there may be exactly what the user wants — so a custom
    endpoint gets no filter at all, and the request goes to that host."""
    seen: List[httpx.Request] = []
    client = _mock_client(
        _openai_handler(_fixture("openai_custom.json"), "llm.example.com", "/v1/models"), seen
    )

    listing = list_openai_models(
        "sk-llm", "https://llm.example.com/v1", http_client=client
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
    assert seen[0].url.host == "llm.example.com"
    assert seen[0].url.path == "/v1/models"
    assert seen[0].headers["authorization"] == "Bearer sk-llm"


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


# gpt-4.1's own `created` (2025-04-10): recent enough that the release-date
# rule never touches a test that is about something else.
OPENAI_MODERN_CREATED = 1744316542


def _openai_list_of(
    ids: List[str], created: int = OPENAI_MODERN_CREATED
) -> Callable[[httpx.Request], httpx.Response]:
    body = {
        "object": "list",
        "data": [
            {"id": i, "object": "model", "created": created, "owned_by": "openai"}
            for i in ids
        ],
    }
    return lambda request: httpx.Response(200, json=body)


def _openai_entries(entries: List[dict]) -> Callable[[httpx.Request], httpx.Response]:
    body = {"object": "list", "data": entries}
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


@pytest.mark.parametrize(
    "base_url",
    [None, "https://api.openai.com/v1", "https://api.openai.com/v1/"],
    ids=["default", "explicit", "trailing-slash"],
)
def test_openai_moves_models_released_before_2025_to_hidden(base_url):
    """Only models from 2025 on are worth choosing for a translation; the older
    ones stay reachable under "show all" because `created` can be wrong and a
    user may still want one. The cutoff is 2025-01-01T00:00:00Z inclusive."""
    client = _mock_client(
        _openai_entries(
            [
                {"id": "gpt-4o", "created": CUTOFF_S - 1},  # 2024-12-31T23:59:59Z
                {"id": "gpt-4.1", "created": CUTOFF_S},  # 2025-01-01T00:00:00Z
                {"id": "gpt-4-turbo", "created": 1712361441},  # 2024-04-06T00:37:21Z
                {"id": "gpt-5", "created": 1754425777},  # 2025-08-05T20:09:37Z
            ]
        ),
        [],
    )

    listing = list_openai_models("sk-test-key", base_url, http_client=client)

    assert [m.id for m in listing.models] == ["gpt-4.1", "gpt-5"]
    assert [m.id for m in listing.hidden] == ["gpt-4o", "gpt-4-turbo"]
    assert listing.models[0].created_at == 1735689600000  # 2025-01-01T00:00:00Z
    assert listing.hidden[0].created_at == 1735689599000  # 2024-12-31T23:59:59Z


def test_openai_keeps_a_model_it_cannot_date():
    """`created` of 0 or none at all says nothing about age, so the model stays
    on offer rather than being hidden on no evidence."""
    client = _mock_client(
        _openai_entries(
            [
                {"id": "gpt-zero", "created": 0},
                {"id": "gpt-undated"},
            ]
        ),
        [],
    )

    listing = list_openai_models("sk-test-key", None, http_client=client)

    assert [m.id for m in listing.models] == ["gpt-zero", "gpt-undated"]
    assert listing.hidden == ()


def test_openai_lists_a_non_chat_model_that_is_also_old_once():
    """`whisper-1` fails both rules; "show all" must not list it twice."""
    client = _mock_client(
        _openai_entries(
            [
                {"id": "whisper-1", "created": 1677532384},  # 2023-02-27T21:13:04Z
                {"id": "gpt-4.1", "created": 1744316542},  # 2025-04-10
            ]
        ),
        [],
    )

    listing = list_openai_models("sk-test-key", None, http_client=client)

    assert [m.id for m in listing.models] == ["gpt-4.1"]
    assert [m.id for m in listing.hidden] == ["whisper-1"]


def test_openai_hidden_keeps_the_providers_order_across_both_rules():
    """The "show all" list reads in the order the provider returned, not
    grouped by why each model was hidden."""
    client = _mock_client(
        _openai_entries(
            [
                {"id": "gpt-4-0613", "created": 1686588896},  # 2023-06-12, old chat
                {"id": "text-embedding-3-large", "created": OPENAI_MODERN_CREATED},
                {"id": "gpt-4.1", "created": OPENAI_MODERN_CREATED},
                {"id": "gpt-4o", "created": 1715367049},  # 2024-05-10, old chat
                {"id": "o3", "created": 1744225308},  # 2025-04-09
                {"id": "tts-1", "created": 1681940951},  # 2023-04-19, old non-chat
            ]
        ),
        [],
    )

    listing = list_openai_models("sk-test-key", None, http_client=client)

    assert [m.id for m in listing.models] == ["gpt-4.1", "o3"]
    assert [m.id for m in listing.hidden] == [
        "gpt-4-0613",
        "text-embedding-3-large",
        "gpt-4o",
        "tts-1",
    ]


@pytest.mark.parametrize(
    "base_url",
    ["https://openrouter.ai/api/v1", "https://llm.example.com/v1"],
    ids=["openrouter", "sk-llm"],
)
def test_openai_release_date_rule_does_not_apply_at_another_endpoint(base_url):
    """A compatible server may stamp `created` with when it loaded a model,
    and OpenRouter serves other vendors' models, so an old `created` there
    says nothing about release."""
    client = _mock_client(_openai_list_of(["llama3.2:latest", "qwen2.5"], created=1), [])

    listing = list_openai_models("key", base_url, http_client=client)

    assert [m.id for m in listing.models] == ["llama3.2:latest", "qwen2.5"]
    assert listing.hidden == ()


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
    ]
    assert [m.id for m in listing.hidden] == ["claude-3-haiku-20240307"]
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
    )
    # Mapped the same way when hidden, since "show all" shares the picker.
    assert listing.hidden == (
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

    assert len(listing.models) == 2
    assert len(listing.hidden) == 1
    assert seen and all(r.url.host == "proxy.example.com" for r in seen)


def _anthropic_models_of(entries: List[dict]):
    body = {"data": entries, "has_more": False}
    return lambda request: httpx.Response(200, json=body)


def _anthropic_entry(model_id: str, created_at: str | None) -> dict:
    entry = {"type": "model", "id": model_id, "display_name": model_id}
    if created_at is not None:
        entry["created_at"] = created_at
    return entry


@pytest.mark.parametrize(
    "base_url", [None, "https://proxy.example.com"], ids=["default", "proxy"]
)
def test_anthropic_moves_models_released_before_2025_to_hidden(base_url):
    """A proxy still serves Anthropic's own ids and dates, so the rule applies
    at any endpoint. The cutoff, 2025-01-01T00:00:00Z, is inclusive."""
    client = _mock_client(
        _anthropic_models_of(
            [
                _anthropic_entry("claude-old", "2024-12-31T23:59:59Z"),
                _anthropic_entry("claude-edge", "2025-01-01T00:00:00Z"),
                _anthropic_entry("claude-3-opus-20240229", "2024-02-29T00:00:00Z"),
                _anthropic_entry("claude-new", "2025-10-15T12:30:00Z"),
            ]
        ),
        [],
    )

    listing = list_anthropic_models("sk-ant-test", base_url, http_client=client)

    assert [m.id for m in listing.models] == ["claude-edge", "claude-new"]
    assert [m.id for m in listing.hidden] == ["claude-old", "claude-3-opus-20240229"]
    assert listing.models[0].created_at == 1735689600000  # 2025-01-01T00:00:00Z
    assert listing.hidden[0].created_at == 1735689599000  # 2024-12-31T23:59:59Z


def test_anthropic_keeps_a_model_without_a_created_at():
    """No date is no evidence of age, so the model stays on offer."""
    client = _mock_client(
        _anthropic_models_of([_anthropic_entry("claude-undated", None)]), []
    )

    listing = list_anthropic_models("sk-ant-test", None, http_client=client)

    assert [m.id for m in listing.models] == ["claude-undated"]
    assert listing.hidden == ()


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


def _gemini_models_of(names: List[str], methods=("generateContent", "countTokens")):
    body = {
        "models": [
            {"name": f"models/{n}", "supportedGenerationMethods": list(methods)}
            for n in names
        ]
    }
    return lambda request: httpx.Response(200, json=body)


def test_gemini_hides_families_older_than_gemini_2_and_gemma_3():
    """The listing carries no date, so the family's version in the id stands in
    for one: Gemini 1.x and Gemma 1/2 are 2024-and-earlier, while Gemini 2.0
    (GA February 2025) and Gemma 3 (March 2025) are kept. Order is the
    provider's, whichever family each model belongs to."""
    ids = [
        "gemini-1.5-pro",
        "gemini-2.5-flash",
        "gemma-2-9b-it",
        "gemini-1.0-pro-vision-latest",
        "gemini-2.0-flash",
        "gemma-3-27b-it",
        "gemini-1.5-flash-8b",
        "gemma-2-27b-it",
        "gemini-3-pro-preview",
    ]
    client = _mock_client(_gemini_models_of(ids), [])

    listing = list_gemini_models("gm-test-key", http_client=client)

    assert [m.id for m in listing.models] == [
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemma-3-27b-it",
        "gemini-3-pro-preview",
    ]
    assert [m.id for m in listing.hidden] == [
        "gemini-1.5-pro",
        "gemma-2-9b-it",
        "gemini-1.0-pro-vision-latest",
        "gemini-1.5-flash-8b",
        "gemma-2-27b-it",
    ]


def test_gemini_reads_gemma_1s_parameter_size_as_gemma_1():
    """Gemma 1 (February 2024) put the parameter size straight after the family
    — `gemma-7b-it` — so the `7` is a size, not "Gemma 7". Gemma 3n's `3n` is
    still Gemma 3."""
    ids = ["gemma-7b-it", "gemma-3n-e4b-it", "gemma-2b-it", "gemma-1.1-7b-it"]
    client = _mock_client(_gemini_models_of(ids), [])

    listing = list_gemini_models("gm-test-key", http_client=client)

    assert [m.id for m in listing.models] == ["gemma-3n-e4b-it"]
    assert [m.id for m in listing.hidden] == ["gemma-7b-it", "gemma-2b-it", "gemma-1.1-7b-it"]


def test_gemini_keeps_ids_it_cannot_date():
    """An alias or an experiment names no version, so hiding it would be a
    guess; it stays on offer."""
    ids = ["gemini-flash-latest", "gemini-exp-1206", "learnlm-2.0-flash-experimental"]
    client = _mock_client(_gemini_models_of(ids), [])

    listing = list_gemini_models("gm-test-key", http_client=client)

    assert [m.id for m in listing.models] == ids
    assert listing.hidden == ()


def test_gemini_still_drops_old_models_without_generate_content_outright():
    """Dropping non-`generateContent` models is a capability fact and happens
    before dating: an old embedding model is in neither bucket."""
    client = _mock_client(
        _gemini_models_of(["gemini-1.0-embedding", "gemini-1.5-pro"], ("embedContent",)),
        [],
    )

    listing = list_gemini_models("gm-test-key", http_client=client)

    assert listing.models == ()
    assert listing.hidden == ()


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
        # ...and a bare name to its tagged one.
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
