"""Asking an endpoint which models a key can use, one function per protocol.

Listing is how a key is verified and how models are discovered (#84): every
provider serves its list for free to any valid key, so a 401/403 here says the
key is wrong without spending a token on a completion.

Blocking, and each imports its SDK inside the function: callers run these off
the event loop, and the SDKs cost seconds to import. Reached through
`ProviderSpec.lister`. `http_client` is the seam the tests use to answer from
recorded responses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Optional, Tuple


@dataclass(frozen=True)
class ListedModel:
    id: str
    display_name: Optional[str] = None
    context_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    # Unix milliseconds, UTC, like every timestamp the stores keep.
    created_at: Optional[int] = None


@dataclass(frozen=True)
class Listing:
    models: Tuple[ListedModel, ...]
    # What the non-chat and release-date filters took out. Both are guesses
    # that can be wrong, so it is kept for a "show all" rather than thrown away.
    hidden: Tuple[ListedModel, ...] = ()


# OpenAI's list is every model the key can reach, with nothing in the response
# that says which ones `chat.completions` — the only call the translator makes —
# will serve. Only the id does. Beyond the embedding, speech and image models,
# that rules out the completions-only `-instruct` models and the Responses-only
# ones (`-pro`, `codex`, `computer-use`, `deep-research`): picked from the
# toolbar, which saves a listed model unchecked, each would 404 on every
# paragraph, and a 404 isn't fatal, so the job would run to the end on source
# text. Words match whole `-`-separated parts, so `o4-mini` and
# `gpt-4o-search-preview` stay on offer.
_OPENAI_NON_CHAT = re.compile(
    r"^(davinci|babbage|dall-e|whisper|sora)"
    r"|(^|-)(tts|embedding|image|moderation|realtime|audio|transcribe|instruct"
    r"|codex|computer-use|deep-research|pro)(-|$)"
)

# Gemini's speech, image and computer-use variants advertise `generateContent`
# like its text models, but answer in audio or images, or need a tool loop.
_GEMINI_NON_TEXT = re.compile(r"-(tts|image|native-audio|computer-use)(-|$)")

# Only models released from here on are offered; older ones go to `hidden`, so
# "show all" still reaches them. The lists run back to 2023, and a picker of
# `gpt-3.5-turbo`, `gpt-4-0613` and Gemini 1.5 buried the models anyone picks.
# Unix ms, UTC: 2025-01-01T00:00:00Z.
RELEASED_FROM = 1_735_689_600_000

# Gemini's listing carries no date, so the family's version stands in for one:
# Gemini 2.0 went GA in February 2025 and Gemma 3 came out in March 2025. An id
# without a version (`gemini-flash-latest`, `gemini-exp-1206`) can't be dated
# and stays offered. Gemma 1 put the parameter size where later releases put
# the version — `gemma-7b-it` — so a number ending in `b` is a size.
_GEMINI_FAMILY = re.compile(r"^(gemini|gemma)-(\d+)(?:\.(\d+))?(b(?:-|$))?")
_GEMINI_FIRST_OFFERED = {"gemini": (2, 0), "gemma": (3, 0)}

# OpenAI's own endpoint, where the id filter above applies. It is sent
# explicitly since #88 (`registry.endpoint_for`), so "no endpoint" can't be
# what marks it; any other server — OpenRouter, Ollama, a proxy — names its
# models its own way.
_OPENAI_API = "https://api.openai.com/v1"

# Both SDKs read a timeout in seconds. A listing is one small GET; the route's
# own deadline (`catalog.PROBE_TIMEOUT_S`) sits above this.
_TIMEOUT_S = 10


def _split(models: Iterable[ListedModel], hide: Callable[[ListedModel], bool]) -> Listing:
    models = tuple(models)
    return Listing(
        models=tuple(m for m in models if not hide(m)),
        hidden=tuple(m for m in models if hide(m)),
    )


def _released_too_early(model: ListedModel) -> bool:
    # An undated model is offered: hiding it would be a guess.
    return model.created_at is not None and model.created_at < RELEASED_FROM


def _gemini_too_early(model: ListedModel) -> bool:
    match = _GEMINI_FAMILY.match(model.id)
    if match is None:
        return False
    family, major, minor, size = match.groups()
    version = (1, 0) if size else (int(major), int(minor or 0))
    return version < _GEMINI_FIRST_OFFERED[family]


def list_openai_models(
    api_key: str, base_url: Optional[str], *, http_client: Any = None
) -> Listing:
    from openai import OpenAI

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        max_retries=0,
        timeout=_TIMEOUT_S,
        http_client=http_client,
    )
    models = [
        ListedModel(
            id=model.id,
            created_at=model.created * 1000 if model.created else None,
        )
        for model in client.models.list()
    ]
    if base_url is not None and base_url.rstrip("/") != _OPENAI_API:
        # Ollama, LM Studio and proxies name models anything; `whisper-…` or
        # `…-audio` there may well be the chat model the user wants. Nor is
        # `created` a release date there: Ollama's is when it was pulled.
        return Listing(models=tuple(models))
    return _split(
        models, lambda m: bool(_OPENAI_NON_CHAT.search(m.id)) or _released_too_early(m)
    )


def list_anthropic_models(
    api_key: str, base_url: Optional[str], *, http_client: Any = None
) -> Listing:
    import anthropic

    client_kwargs: dict = {"api_key": api_key, "max_retries": 0, "timeout": _TIMEOUT_S}
    if base_url:
        client_kwargs["base_url"] = base_url
    if http_client is not None:
        client_kwargs["http_client"] = http_client
    client = anthropic.Anthropic(**client_kwargs)

    # Walked page by page rather than trusting the page object's iterator: the
    # old `list(limit=100)` read as one page, and the next hundred models
    # arriving would have gone unseen without a test that pins two pages.
    models = []
    after_id: Optional[str] = None
    while True:
        params: dict = {"limit": 100}
        if after_id:
            params["after_id"] = after_id
        page = client.models.list(**params)
        for model in page.data:
            created = getattr(model, "created_at", None)
            models.append(
                ListedModel(
                    id=model.id,
                    display_name=getattr(model, "display_name", None),
                    context_tokens=getattr(model, "max_input_tokens", None),
                    output_tokens=getattr(model, "max_tokens", None),
                    created_at=_ms(created) if created else None,
                )
            )
        # A page that doesn't advance — a misbehaving proxy — ends the walk:
        # the route's deadline abandons the result, not this thread.
        if not page.has_more or not page.last_id or page.last_id == after_id:
            return _split(models, _released_too_early)
        after_id = page.last_id


def list_gemini_models(
    api_key: str, base_url: Optional[str] = None, *, http_client: Any = None
) -> Listing:
    from google import genai
    from google.genai import types as genai_types

    client = genai.Client(
        api_key=api_key,
        http_options=genai_types.HttpOptions(
            timeout=_TIMEOUT_S * 1000, httpx_client=http_client
        ),
    )
    models = []
    # The pager fetches the next page as iteration reaches it.
    for model in client.models.list():
        # Unlike an id heuristic this is the API's own statement of what a
        # model does, so the rest (embeddings, Imagen) are dropped, not hidden.
        if "generateContent" not in (model.supported_actions or ()):
            continue
        models.append(
            ListedModel(
                id=(model.name or "").removeprefix("models/"),
                display_name=model.display_name,
                context_tokens=model.input_token_limit,
                output_tokens=model.output_token_limit,
            )
        )
    return _split(
        models, lambda m: bool(_GEMINI_NON_TEXT.search(m.id)) or _gemini_too_early(m)
    )


def model_matches(model: str, ids: Iterable[str]) -> bool:
    """Whether a saved model name is one of `ids`, allowing for aliases.

    It exists to catch a typo before a whole document fails on it, so it errs
    toward "found": `claude-haiku-4-5` is Anthropic's alias for the listed
    `claude-haiku-4-5-20251001`, and Ollama serves `llama3.2` as the listed
    `llama3.2:latest`.
    """
    return any(
        listed == model or (listed.startswith(model) and listed[len(model)] in "-:")
        for listed in ids
    )


def _ms(value: Any) -> int:
    """A datetime, or an RFC 3339 string, as Unix milliseconds."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(value.timestamp() * 1000)
