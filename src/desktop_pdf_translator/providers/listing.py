"""Asking an endpoint which models it serves, one function per protocol.

Blocking, and each imports its SDK inside the function: callers run these off
the event loop, and the SDKs cost seconds to import. Reached through
`ProviderSpec.lister`.
"""

from __future__ import annotations

from typing import List, Optional


def list_openai_models(api_key: str, base_url: Optional[str]) -> List[str]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0, timeout=10)
    return [model.id for model in client.models.list()]


def list_anthropic_models(api_key: str, base_url: Optional[str]) -> List[str]:
    import anthropic

    client_kwargs: dict = {"api_key": api_key, "max_retries": 0, "timeout": 10}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = anthropic.Anthropic(**client_kwargs)
    return [model.id for model in client.models.list(limit=100)]
