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
from typing import Any, Iterable, Optional, Tuple


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
    # What the non-chat filter took out. A heuristic on ids can be wrong, so
    # it is kept for a "show all" rather than thrown away.
    hidden: Tuple[ListedModel, ...] = ()


# OpenAI's list is every model the key can reach — embeddings, speech, images —
# with nothing in the response that says which ones chat. Only the id does.
_OPENAI_NON_CHAT = re.compile(
    r"^(text-embedding-|tts-|whisper-|dall-e-|gpt-image-|davinci|babbage)"
    r"|moderation|-realtime|-audio|-transcribe"
)

# Both SDKs read a timeout in seconds. A listing is one small GET; the route's
# own deadline (`catalog.PROBE_TIMEOUT_S`) sits above this.
_TIMEOUT_S = 10


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
    if base_url is not None:
        # Ollama, LM Studio and proxies name models anything; `whisper-…` or
        # `…-audio` there may well be the chat model the user wants.
        return Listing(models=tuple(models))
    return Listing(
        models=tuple(m for m in models if not _OPENAI_NON_CHAT.search(m.id)),
        hidden=tuple(m for m in models if _OPENAI_NON_CHAT.search(m.id)),
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
            return Listing(models=tuple(models))
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
        # Unlike OpenAI's id heuristic this is the API's own statement of what
        # a model does, so the rest (embeddings, Imagen) are dropped, not hidden.
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
    return Listing(models=tuple(models))


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
