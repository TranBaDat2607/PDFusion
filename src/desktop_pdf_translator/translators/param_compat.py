"""Request parameters an endpoint refused, remembered per model (#32).

The model name is free text, and the endpoint can be any server that speaks a
provider's API. Neither guarantees that a request shaped for one model suits
another: Claude Opus 4.7 and later answer 400 to a non-default `temperature`,
and OpenAI's reasoning models refuse `temperature` and want
`max_completion_tokens` where older ones took `max_tokens`. PDFusion sends
those on every paragraph, so without this such a model fails the whole
document one paragraph at a time.

`BaseTranslator._call_adapting` is the only caller. It asks `rejected_param`
whether a failure was one of these refusals, records it here, and sends the
request again without that parameter. The record is process-wide and keyed by
service, endpoint and model, so the next job — and the next paragraph on every
worker thread — never repeats the refused request. Nothing persists it across a
restart: a server that is upgraded, or a model name reused by another server,
gets asked afresh.

Stdlib only. `translators/base.py` imports this, and it is on the boot path.
"""

from __future__ import annotations

import threading
from typing import Dict, FrozenSet, Iterable, Optional, Set, Tuple

# (service, base_url, model). `base_url` is `None` for the provider's own
# endpoint, so Ollama serving a model under an OpenAI name never shares a
# record with OpenAI.
EndpointKey = Tuple[str, Optional[str], str]

# Words a 400 uses when a parameter itself is refused. Needed alongside the
# parameter's name, which also appears in refusals of its *value*: "max_tokens
# is too large" is fixed by a smaller number, not by leaving the field out.
_REFUSAL_MARKERS = (
    "unsupported",
    "not supported",
    "does not support",
    "only the default",
    "deprecated",
    "not permitted",
    "not allowed",
)

_REJECTED: Dict[EndpointKey, Set[str]] = {}
_LOCK = threading.Lock()


def rejected_params(key: EndpointKey) -> FrozenSet[str]:
    """Parameters this endpoint refused for this model, so far."""
    with _LOCK:
        return frozenset(_REJECTED.get(key, ()))


def remember_rejected(key: EndpointKey, param: str) -> None:
    with _LOCK:
        _REJECTED.setdefault(key, set()).add(param)


def rejected_param(
    status: Optional[int], message: str, candidates: Iterable[str]
) -> Optional[str]:
    """The candidate a failed request was refused for, or `None`.

    Only a 400 whose message both names the parameter and says it isn't
    accepted. Everything else — a rejected key, a rate limit, a value that is
    merely out of range — is a failure to report, not a request to reshape.
    """
    if status != 400:
        return None
    text = message.lower()
    if not any(marker in text for marker in _REFUSAL_MARKERS):
        return None
    for param in candidates:
        if param in text:
            return param
    return None
