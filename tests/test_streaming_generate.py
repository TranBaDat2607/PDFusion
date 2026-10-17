"""`generate(on_text=...)` hands the answer to the caller while it is written.

A chat answer takes seconds to finish; the panel shows it as it grows. With
`on_text` given, each LLM backend asks its SDK for a stream and calls
`on_text(offset, piece)` per non-empty piece. `offset` is the number of
characters this *attempt* has delivered so far, not a running total across
retries: a retry restarts the stream, and a consumer doing
`text = text[:offset] + piece` must end with the retry's text alone rather than
a duplicated prefix. The SDK clients are replaced by fakes that script a stream
(or fail in the middle of one), so nothing touches the network.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from google.genai import types as genai_types

from desktop_pdf_translator.translators import (
    anthropic_translator,
    gemini_translator,
    openai_translator,
    rate_limiter,
)
from desktop_pdf_translator.translators.anthropic_translator import AnthropicTranslator
from desktop_pdf_translator.translators.base import PageImage
from desktop_pdf_translator.translators.gemini_translator import GeminiTranslator
from desktop_pdf_translator.translators.openai_translator import OpenAITranslator

PROMPT = "What does the figure show?"
SYSTEM = "Answer from the pages."
PAGE = PageImage(data=b"\xff\xd8\xff-page-\x00\x01", mime_type="image/jpeg", label="Page 3")


class APIConnectionError(Exception):
    """Stands in for the SDKs' transport error. `_call_with_backoff` matches
    retryable errors by class *name* along the MRO, so the name is the contract."""


@pytest.fixture(autouse=True)
def _fresh_state():
    rate_limiter._LIMITERS.clear()
    yield
    rate_limiter._LIMITERS.clear()


class _Collector:
    """What a chat panel does with `on_text`, kept as a list of calls too so a
    test can see the offsets as well as the text they rebuild."""

    def __init__(self):
        self.calls: List[tuple] = []
        self.text = ""

    def __call__(self, offset: int, piece: str) -> None:
        self.calls.append((offset, piece))
        self.text = self.text[:offset] + piece


class _Attempt:
    """One scripted request: the pieces it streams, then either `error` raised
    mid-stream (a dropped connection) or a clean end with `finish`.

    A piece of None is a chunk that carries no text (Gemini's `.text` and
    OpenAI's `delta.content` are both None on such chunks); "" is an empty one.
    """

    def __init__(self, pieces, finish: str = "stop", error: Optional[Exception] = None):
        self.pieces = pieces
        self.finish = finish  # "stop" or "length"
        self.error = error


class _Backend:
    def __init__(self, attempts: List[_Attempt]):
        self.attempts = list(attempts)
        self.requests: List[Dict[str, Any]] = []
        self.streamed: List[bool] = []

    def _next_attempt(self) -> _Attempt:
        assert self.attempts, "the code under test made more requests than scripted"
        return self.attempts.pop(0)


class _OpenAIBackend(_Backend):
    name = "openai"

    def __init__(self, attempts):
        super().__init__(attempts)
        self.translator = OpenAITranslator("en", "vi", api_key="sk-test", model="gpt-test")
        completions = SimpleNamespace(create=self._create)
        self.translator.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    @staticmethod
    def _chunk(content, finish_reason=None):
        delta = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)])

    def _stream(self, attempt: _Attempt):
        for piece in attempt.pieces:
            yield self._chunk(piece)
        if attempt.error:
            raise attempt.error
        # A usage-only chunk has no choices and arrives after the last one.
        yield self._chunk(None, "length" if attempt.finish == "length" else "stop")
        yield SimpleNamespace(choices=[])

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        self.streamed.append(kwargs.get("stream") is True)
        attempt = self._next_attempt()
        if kwargs.get("stream"):
            return self._stream(attempt)
        message = SimpleNamespace(content="".join(p or "" for p in attempt.pieces))
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=attempt.finish)])

    def stream_kwarg(self, request):
        return request.get("stream")

    def system_sent(self):
        first = self.requests[0]["messages"][0]
        return first["content"] if first["role"] == "system" else None

    def image_count(self):
        content = self.requests[0]["messages"][-1]["content"]
        return sum(1 for part in content if part["type"] == "image_url")


class _AnthropicBackend(_Backend):
    name = "anthropic"

    def __init__(self, attempts):
        super().__init__(attempts)
        self.translator = AnthropicTranslator("en", "vi", api_key="sk-test", model="claude-test")
        self.translator.client = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _stream(self, attempt: _Attempt):
        yield SimpleNamespace(type="message_start", message=SimpleNamespace(content=[]))
        yield SimpleNamespace(type="content_block_start", index=0, content_block=SimpleNamespace(type="text"))
        for piece in attempt.pieces:
            if isinstance(piece, tuple):  # ("thinking", text): reasoning, not answer
                delta = SimpleNamespace(type="thinking_delta", thinking=piece[1])
            else:
                delta = SimpleNamespace(type="text_delta", text=piece or "")
            yield SimpleNamespace(type="content_block_delta", index=0, delta=delta)
        if attempt.error:
            raise attempt.error
        yield SimpleNamespace(type="content_block_stop", index=0)
        stop = "max_tokens" if attempt.finish == "length" else "end_turn"
        yield SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason=stop))
        yield SimpleNamespace(type="message_stop")

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        self.streamed.append(kwargs.get("stream") is True)
        attempt = self._next_attempt()
        if kwargs.get("stream"):
            return self._stream(attempt)
        text = "".join(p or "" for p in attempt.pieces)
        stop = "max_tokens" if attempt.finish == "length" else "end_turn"
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop)

    def stream_kwarg(self, request):
        return request.get("stream")

    def system_sent(self):
        return self.requests[0].get("system")

    def image_count(self):
        content = self.requests[0]["messages"][0]["content"]
        return sum(1 for block in content if block["type"] == "image")


class _GeminiBackend(_Backend):
    name = "gemini"

    def __init__(self, attempts):
        super().__init__(attempts)
        self.translator = GeminiTranslator("en", "vi", api_key="key-test", model="gemini-test")
        models = SimpleNamespace(
            generate_content=self._generate_content,
            generate_content_stream=self._generate_content_stream,
        )
        self.translator.client = SimpleNamespace(models=models)

    @staticmethod
    def _candidates(finish: Optional[str]):
        if finish is None:
            return []
        reason = genai_types.FinishReason.MAX_TOKENS if finish == "length" else genai_types.FinishReason.STOP
        return [SimpleNamespace(finish_reason=reason)]

    def _stream(self, attempt: _Attempt):
        for piece in attempt.pieces:
            yield SimpleNamespace(text=piece, candidates=[])
        if attempt.error:
            raise attempt.error
        yield SimpleNamespace(text=None, candidates=self._candidates(attempt.finish))

    def _generate_content_stream(self, **kwargs):
        self.requests.append(kwargs)
        self.streamed.append(True)
        return self._stream(self._next_attempt())

    def _generate_content(self, **kwargs):
        self.requests.append(kwargs)
        self.streamed.append(False)
        attempt = self._next_attempt()
        text = "".join(p or "" for p in attempt.pieces)
        return SimpleNamespace(text=text, candidates=self._candidates(attempt.finish))

    def stream_kwarg(self, request):
        # Gemini streams by calling another method, not by a keyword.
        return None

    def system_sent(self):
        return self.requests[0]["config"].system_instruction

    def image_count(self):
        contents = self.requests[0]["contents"]
        return sum(1 for part in contents if isinstance(part, genai_types.Part))


BACKENDS = [_OpenAIBackend, _AnthropicBackend, _GeminiBackend]


@pytest.fixture(params=BACKENDS, ids=lambda cls: cls.name)
def make_backend(request, monkeypatch: pytest.MonkeyPatch):
    for module in (openai_translator, anthropic_translator, gemini_translator):
        monkeypatch.setattr(module, "_llm_cache_get", lambda *a, **k: None)
        monkeypatch.setattr(module, "_llm_cache_set", lambda *a, **k: None)

    def build(*attempts: _Attempt):
        backend = request.param(list(attempts))
        # A retry backs off for a second or more; the wait is not under test.
        backend.translator._sleep_or_cancel = lambda seconds: False
        return backend

    return build


def test_each_piece_arrives_in_order_with_the_characters_delivered_before_it(make_backend):
    backend = make_backend(_Attempt(["The ", "answer", " is 42."]))
    got = _Collector()

    backend.translator.generate(PROMPT, on_text=got)

    assert got.calls == [(0, "The "), (4, "answer"), (10, " is 42.")]


def test_a_consumer_rebuilds_the_returned_text_from_the_pieces(make_backend):
    # Whitespace at both ends: offsets count the raw characters, while the
    # return value is stripped.
    backend = make_backend(_Attempt(["  The ", "answer ", "\n"]))
    got = _Collector()

    answer = backend.translator.generate(PROMPT, on_text=got)

    assert got.calls == [(0, "  The "), (6, "answer "), (13, "\n")]
    assert got.text.strip() == answer == "The answer"


def test_empty_and_textless_chunks_are_not_delivered(make_backend):
    backend = make_backend(_Attempt(["", "Hi", None, " there", ""]))
    got = _Collector()

    answer = backend.translator.generate(PROMPT, on_text=got)

    assert got.calls == [(0, "Hi"), (2, " there")]
    assert answer == "Hi there"


def test_a_stream_with_no_text_returns_none_and_never_calls_back(make_backend):
    backend = make_backend(_Attempt([None, ""]))
    got = _Collector()

    answer = backend.translator.generate(PROMPT, on_text=got)

    assert answer is None
    assert got.calls == []


def test_openai_skips_a_chunk_without_choices_between_pieces():
    """OpenAI sends a usage chunk with `choices=[]` when asked for usage;
    indexing `choices[0]` on it would raise IndexError."""
    backend = _OpenAIBackend([])
    backend.translator._sleep_or_cancel = lambda seconds: False

    def chunks():
        yield _OpenAIBackend._chunk("Hello")
        yield SimpleNamespace(choices=[])
        yield _OpenAIBackend._chunk(" world")
        yield _OpenAIBackend._chunk(None, "stop")
        yield SimpleNamespace(choices=[])

    def create(**kwargs):
        backend.requests.append(kwargs)
        return chunks()

    backend.translator.client.chat.completions.create = create
    got = _Collector()

    answer = backend.translator.generate(PROMPT, on_text=got)

    assert got.calls == [(0, "Hello"), (5, " world")]
    assert answer == "Hello world"


def test_anthropic_thinking_is_not_answer_text():
    backend = _AnthropicBackend([_Attempt([("thinking", "Let me look. "), "The ", ("thinking", "Hmm."), "answer"])])
    got = _Collector()

    answer = backend.translator.generate(PROMPT, on_text=got)

    assert got.calls == [(0, "The "), (4, "answer")]
    assert answer == "The answer"


def test_the_request_streams_when_on_text_is_given(make_backend):
    backend = make_backend(_Attempt(["ok"]))

    backend.translator.generate(PROMPT, on_text=_Collector())

    # OpenAI and Anthropic ask with `stream=True`; Gemini uses its own stream
    # method, which the fake records as a streamed request.
    assert backend.streamed == [True]
    if backend.name != "gemini":
        assert backend.stream_kwarg(backend.requests[0]) is True


def test_gemini_streams_through_generate_content_stream_only():
    backend = _GeminiBackend([_Attempt(["ok"])])
    called = []
    models = backend.translator.client.models
    models.generate_content = lambda **kw: called.append("generate_content")

    backend.translator.generate(PROMPT, on_text=_Collector())

    assert called == []
    assert backend.streamed == [True]


def test_regression_guard_without_on_text_the_request_is_not_streamed(make_backend):
    """A guard, not a red test: passes today and must keep passing, so the
    translate path and every caller that wants no streaming send what they sent."""
    backend = make_backend(_Attempt(["the answer"]))

    answer = backend.translator.generate(PROMPT)

    assert answer == "the answer"
    assert backend.streamed == [False]
    assert "stream" not in backend.requests[0]


def test_a_stream_cut_off_at_the_token_limit_raises(make_backend):
    backend = make_backend(_Attempt(["A half-written sen"], finish="length"))

    with pytest.raises(RuntimeError):
        backend.translator.generate(PROMPT, on_text=_Collector())


def test_regression_guard_a_plain_request_cut_off_at_the_token_limit_raises(make_backend):
    """A guard: the non-streamed path already raises, and streaming must match it."""
    backend = make_backend(_Attempt(["A half-written sen"], finish="length"))

    with pytest.raises(RuntimeError):
        backend.translator.generate(PROMPT)


def test_a_dropped_connection_mid_stream_is_retried_from_offset_zero(make_backend):
    backend = make_backend(
        _Attempt(["Hel", "lo"], error=APIConnectionError("connection reset")),
        _Attempt(["Hello", " world"]),
    )
    got = _Collector()

    answer = backend.translator.generate(PROMPT, on_text=got)

    assert len(backend.requests) == 2
    assert got.calls == [(0, "Hel"), (3, "lo"), (0, "Hello"), (5, " world")]
    # The consumer's text is the retry's alone, with no stale or doubled prefix.
    assert got.text == "Hello world"
    assert answer == "Hello world"


class APIError(Exception):
    """The SDKs' base API error. Raised from inside an accepted stream it
    carries no useful status: Anthropic's `overloaded_error` event comes with
    the stream's own 200, OpenAI's `{"error": ...}` chunk with none."""

    def __init__(self, message: str, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class APIStatusError(APIError):
    pass


@pytest.mark.parametrize(
    "error",
    [APIStatusError("Overloaded", status_code=200), APIError("upstream error", status_code=None)],
    ids=["status-200", "no-status"],
)
def test_an_api_error_inside_an_accepted_stream_is_retried(make_backend, error):
    """Sent instead as a 529 before the stream began, the same overload
    would have been retried; inside the stream it must be too."""
    backend = make_backend(
        _Attempt(["Hel"], error=error),
        _Attempt(["Hello"]),
    )
    got = _Collector()

    answer = backend.translator.generate(PROMPT, on_text=got)

    assert len(backend.requests) == 2
    assert got.text == "Hello"
    assert answer == "Hello"


def test_a_rejected_key_inside_a_stream_is_not_retried(make_backend):
    backend = make_backend(
        _Attempt(["Hel"], error=APIStatusError("invalid x-api-key", status_code=401)),
        _Attempt(["never reached"]),
    )

    with pytest.raises(APIStatusError):
        backend.translator.generate(PROMPT, on_text=_Collector())

    assert len(backend.requests) == 1


def test_a_failure_that_is_not_retryable_surfaces_after_its_pieces(make_backend):
    backend = make_backend(
        _Attempt(["Hel"], error=ValueError("bad chunk")),
        _Attempt(["never reached"]),
    )
    got = _Collector()

    with pytest.raises(ValueError, match="bad chunk"):
        backend.translator.generate(PROMPT, on_text=got)

    assert len(backend.requests) == 1


def test_the_system_prompt_is_sent_when_streaming(make_backend):
    backend = make_backend(_Attempt(["ok"]))

    backend.translator.generate(PROMPT, system=SYSTEM, on_text=_Collector())

    assert backend.system_sent() == SYSTEM


def test_images_are_sent_when_streaming(make_backend):
    backend = make_backend(_Attempt(["ok"]))

    backend.translator.generate(PROMPT, images=[PAGE, PAGE], on_text=_Collector())

    assert backend.image_count() == 2
