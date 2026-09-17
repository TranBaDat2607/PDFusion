"""A parameter an endpoint refuses for one model is left out, and remembered (#32).

Claude Opus 4.7 and later answer 400 to a non-default `temperature`, and
OpenAI's reasoning models are documented to refuse `temperature` and
`max_tokens`. PDFusion sends both on every paragraph, and the model field is
free text now, so nothing stops a user naming such a model, or a local server
with quirks of its own.

The refusal messages below are modelled on OpenAI's documented wording. What
Anthropic's API says for `temperature` has not been captured, so its test uses
a stand-in with the same markers.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Dict, List, Set

import pytest

from desktop_pdf_translator.translators import (
    anthropic_translator,
    openai_translator,
    param_compat,
    rate_limiter,
)
from desktop_pdf_translator.translators.anthropic_translator import AnthropicTranslator
from desktop_pdf_translator.translators.base import BaseTranslator
from desktop_pdf_translator.translators.openai_translator import OpenAITranslator

TEMPERATURE_REFUSED = (
    "Error code: 400 - Unsupported value: 'temperature' does not support 0.3 "
    "with this model. Only the default (1) value is supported."
)
MAX_TOKENS_REFUSED = (
    "Error code: 400 - Unsupported parameter: 'max_tokens' is not supported "
    "with this model. Use 'max_completion_tokens' instead."
)


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch: pytest.MonkeyPatch):
    """Both registries are process-wide, and the paragraph cache would answer
    a repeated text without any request at all."""
    param_compat._REJECTED.clear()
    rate_limiter._LIMITERS.clear()
    for module in (openai_translator, anthropic_translator):
        monkeypatch.setattr(module, "_llm_cache_get", lambda *args, **kwargs: None)
        monkeypatch.setattr(module, "_llm_cache_set", lambda *args, **kwargs: None)
    yield
    param_compat._REJECTED.clear()
    rate_limiter._LIMITERS.clear()


class _SdkError(Exception):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class _Endpoint(BaseTranslator):
    """A server that refuses the parameters named in `refuses`, with the
    message given there."""

    def _setup_translator(self, **kwargs):
        self.model = kwargs["model"]
        self.base_url = kwargs.get("base_url")
        self.refuses: Dict[str, str] = kwargs.get("refuses", {})
        self.sent: List[Set[str]] = []

    def translate(self, text: str) -> str:
        return text

    def request(self):
        return self._call_adapting(self._send, ("temperature", "max_tokens"))

    def _send(self, rejected):
        params = [name for name in ("temperature", "max_tokens") if name not in rejected]
        self.sent.append(set(params))
        for name in params:
            if name in self.refuses:
                raise _SdkError(self.refuses[name], 400)
        return "ok"


def _endpoint(**kwargs) -> _Endpoint:
    return _Endpoint(lang_in="en", lang_out="vi", **kwargs)


# ---------------------------------------------------------------------------
# BaseTranslator._call_adapting
# ---------------------------------------------------------------------------


def test_a_refused_temperature_is_left_out_and_the_request_sent_again():
    endpoint = _endpoint(model="reasoner", refuses={"temperature": TEMPERATURE_REFUSED})

    assert endpoint.request() == "ok"
    assert endpoint.sent == [{"temperature", "max_tokens"}, {"max_tokens"}]


def test_the_refusal_is_remembered_for_that_model_on_that_endpoint():
    _endpoint(model="reasoner", refuses={"temperature": TEMPERATURE_REFUSED}).request()

    later = _endpoint(model="reasoner", refuses={"temperature": TEMPERATURE_REFUSED})
    later.request()

    assert later.sent == [{"max_tokens"}]


@pytest.mark.parametrize(
    "other",
    [
        {"model": "another"},
        {"model": "reasoner", "base_url": "http://localhost:11434/v1"},
    ],
)
def test_another_model_or_endpoint_is_asked_afresh(other: dict):
    _endpoint(model="reasoner", refuses={"temperature": TEMPERATURE_REFUSED}).request()

    fresh = _endpoint(**other)
    fresh.request()

    assert fresh.sent == [{"temperature", "max_tokens"}]


def test_each_refused_parameter_is_left_out_in_turn():
    endpoint = _endpoint(
        model="reasoner",
        refuses={"temperature": TEMPERATURE_REFUSED, "max_tokens": MAX_TOKENS_REFUSED},
    )

    assert endpoint.request() == "ok"
    assert endpoint.sent == [{"temperature", "max_tokens"}, {"max_tokens"}, set()]


def test_a_refusal_that_repeats_without_the_parameter_is_raised():
    """Each parameter is left out once; a server that still complains isn't
    asked forever."""

    class _Stubborn(_Endpoint):
        def _send(self, rejected):
            self.sent.append(set(rejected))
            raise _SdkError(TEMPERATURE_REFUSED, 400)

    endpoint = _Stubborn(lang_in="en", lang_out="vi", model="reasoner")

    with pytest.raises(_SdkError):
        endpoint.request()
    assert len(endpoint.sent) == 2


def test_a_400_about_a_value_is_not_a_refusal():
    """"max_tokens is too large" wants a smaller number, not no field."""
    endpoint = _endpoint(
        model="small",
        refuses={
            "max_tokens": "Error code: 400 - max_tokens is too large: 4000. "
            "This model supports at most 2048 completion tokens"
        },
    )

    with pytest.raises(_SdkError):
        endpoint.request()
    assert len(endpoint.sent) == 1
    assert param_compat.rejected_params(endpoint._endpoint_key()) == frozenset()


def test_only_a_400_can_be_a_refusal():
    assert param_compat.rejected_param(400, TEMPERATURE_REFUSED, ["temperature"]) == "temperature"
    assert param_compat.rejected_param(404, TEMPERATURE_REFUSED, ["temperature"]) is None
    assert param_compat.rejected_param(None, TEMPERATURE_REFUSED, ["temperature"]) is None
    assert param_compat.rejected_param(400, TEMPERATURE_REFUSED, ["max_tokens"]) is None


# ---------------------------------------------------------------------------
# the backends
# ---------------------------------------------------------------------------


class _ReasoningCompletions:
    """`client.chat.completions` for a model that takes no temperature and
    wants `max_completion_tokens`."""

    def __init__(self) -> None:
        self.calls: List[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if "temperature" in kwargs:
            raise _SdkError(TEMPERATURE_REFUSED, 400)
        if "max_tokens" in kwargs:
            raise _SdkError(MAX_TOKENS_REFUSED, 400)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Xin chào"))]
        )


def _openai(completions: _ReasoningCompletions) -> OpenAITranslator:
    translator = OpenAITranslator(
        lang_in="en", lang_out="vi", api_key="sk-test", model="gpt-5.6-luna", max_tokens=4000
    )
    translator.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return translator


def test_openai_translates_with_a_reasoning_model():
    completions = _ReasoningCompletions()
    translator = _openai(completions)

    assert translator.translate("Hello") == "Xin chào"
    assert translator.failed_translations == 0
    last = completions.calls[-1]
    assert "temperature" not in last and "max_tokens" not in last
    assert last["max_completion_tokens"] == 4000


def test_openai_validation_sends_what_translation_sends():
    completions = _ReasoningCompletions()
    translator = _openai(completions)

    assert translator.validate_configuration() == (True, "Configuration is valid")
    completions.calls.clear()
    translator.translate("Hello")

    assert len(completions.calls) == 1


def test_openai_sends_no_token_cap_when_none_is_set():
    assert OpenAITranslator._sampling_kwargs(frozenset(), 0.3, None) == {"temperature": 0.3}


class _NoTemperatureMessages:
    def __init__(self) -> None:
        self.calls: List[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if "temperature" in kwargs:
            raise _SdkError(
                "Error code: 400 - temperature is not supported for this model", 400
            )
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="Xin chào")])


def test_anthropic_reaches_a_model_that_refuses_temperature():
    messages = _NoTemperatureMessages()
    translator = AnthropicTranslator(
        lang_in="en", lang_out="vi", api_key="sk-ant-test", model="claude-opus-5"
    )
    translator.client = SimpleNamespace(messages=messages)

    assert translator.translate("Hello") == "Xin chào"
    assert ["temperature" in call for call in messages.calls] == [True, False]
    assert translator.generate("What does it say?") == "Xin chào"
    assert "temperature" not in messages.calls[-1]
