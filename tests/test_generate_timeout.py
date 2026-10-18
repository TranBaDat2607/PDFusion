"""`generate()` waits as long as its prompt needs, not a fixed 60 s / 30 s (#110).

The chat reads a whole document (up to ~100k tokens, plus page images) in one
request. A provider spends longer than a minute just reading a prompt that big
before the first token comes back, so the fixed timeout fired, the request was
retried, and the retry paid for the whole prompt again. `generate_timeout_s`
scales the wait with the input and is capped, so a request that is truly hung
still ends. A short prompt keeps today's 60 s, and `translate()` (one paragraph
per call) keeps its own short timeouts: a hung paragraph should fail fast.

The tests pin properties of the estimate (monotonic, bounded, images count), not
its formula, so the constants can be re-tuned without rewriting them. The SDK
clients are replaced by fakes that record the request, so nothing touches the
network.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from google.genai import types as genai_types

from desktop_pdf_translator.translators import (
    anthropic_translator,
    base,
    gemini_translator,
    openai_translator,
    rate_limiter,
)
from desktop_pdf_translator.translators.anthropic_translator import AnthropicTranslator
from desktop_pdf_translator.translators.base import PageImage
from desktop_pdf_translator.translators.gemini_translator import GeminiTranslator
from desktop_pdf_translator.translators.openai_translator import OpenAITranslator

SHORT_PROMPT = "What does the figure show?"
SYSTEM = "Answer from the pages."
# 4 characters ~ 1 token, so this is the ~100k-token whole-document read.
WHOLE_DOCUMENT = "lorem ipsum dolor sit amet. " * 14_286  # ~400,000 characters
PAGE = PageImage(data=b"x" * 100_000, label="Page 1")


@pytest.fixture(autouse=True)
def _fresh_state():
    rate_limiter._LIMITERS.clear()
    yield
    rate_limiter._LIMITERS.clear()


# --- the estimate itself ----------------------------------------------------


def test_a_short_prompt_keeps_todays_sixty_seconds():
    assert base.generate_timeout_s(SHORT_PROMPT, None, None) == 60.0
    assert base.generate_timeout_s(SHORT_PROMPT, SYSTEM, None) == 60.0


def test_the_timeout_never_shrinks_as_the_prompt_grows():
    sizes = [0, 100, 1_000, 10_000, 50_000, 100_000, 200_000, 400_000, 1_000_000, 10_000_000]
    values = [base.generate_timeout_s("x" * n, None, None) for n in sizes]

    assert values == sorted(values)
    assert values[-1] > values[0], "a bigger prompt never got any more time"


def test_adding_a_system_prompt_never_shrinks_the_timeout():
    for n in (100, 10_000, 200_000, 400_000):
        prompt = "x" * n
        assert base.generate_timeout_s(prompt, "s" * 50_000, None) >= base.generate_timeout_s(prompt, None, None)


def test_adding_images_never_shrinks_the_timeout():
    for n in (100, 400_000):
        prompt = "x" * n
        previous = base.generate_timeout_s(prompt, None, None)
        for count in (1, 5, 20, 40, 200):
            current = base.generate_timeout_s(prompt, None, [PAGE] * count)
            assert current >= previous
            previous = current


def test_a_hundred_thousand_token_prompt_gets_three_to_five_minutes():
    seconds = base.generate_timeout_s(WHOLE_DOCUMENT, None, None)

    assert 180.0 <= seconds <= 300.0


def test_the_timeout_is_capped_at_five_minutes():
    assert base.generate_timeout_s("x" * 10_000_000, None, None) == 300.0
    # Images and a system prompt add to the estimate but not past the cap.
    assert base.generate_timeout_s("x" * 400_000, "s" * 1_000_000, [PAGE] * 200) == 300.0


def test_enough_page_images_lengthen_the_wait_for_a_short_prompt():
    images = [PageImage(data=b"x" * 100_000, label=f"Page {i}") for i in range(40)]

    assert base.generate_timeout_s(SHORT_PROMPT, None, images) > 60.0


def test_cjk_text_counts_a_token_a_character():
    """A Chinese whole read of 80,000 characters is ~80,000 tokens (60 s plus
    1.5 s for each 1,000 beyond the first 10,000 = 165 s); counting four
    characters a token gave it a quarter of the time it needs."""
    assert base.generate_timeout_s("中" * 80_000, None, None) == 165.0


# --- what each backend sends ------------------------------------------------


class _Stream:
    """What an OpenAI or Anthropic `create(stream=True)` returns: iterable, and
    closable, which `_generate_streaming` relies on to release the connection
    of an attempt that fails partway (#112). A bare `iter()` has no `close`."""

    def __init__(self, chunks):
        self._chunks = iter(chunks)

    def __iter__(self):
        return self._chunks

    def close(self) -> None:
        pass


class _OpenAIBackend:
    name = "openai"

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []
        self.translator = OpenAITranslator("en", "vi", api_key="sk-test", model="gpt-test")
        completions = SimpleNamespace(create=self._create)
        self.translator.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return _Stream(
                [
                    SimpleNamespace(
                        choices=[SimpleNamespace(delta=SimpleNamespace(content="ok"), finish_reason="stop")]
                    )
                ]
            )
        message = SimpleNamespace(content="ok")
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])

    def timeout_s(self) -> float:
        return self.calls[-1]["timeout"]


class _AnthropicBackend:
    name = "anthropic"

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []
        self.translator = AnthropicTranslator("en", "vi", api_key="sk-test", model="claude-test")
        self.translator.client = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return _Stream(
                [
                    SimpleNamespace(type="message_start", message=SimpleNamespace(content=[])),
                    SimpleNamespace(
                        type="content_block_start", index=0, content_block=SimpleNamespace(type="text")
                    ),
                    SimpleNamespace(
                        type="content_block_delta",
                        index=0,
                        delta=SimpleNamespace(type="text_delta", text="ok"),
                    ),
                    SimpleNamespace(type="content_block_stop", index=0),
                    SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason="end_turn")),
                    SimpleNamespace(type="message_stop"),
                ]
            )
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn")

    def timeout_s(self) -> float:
        return self.calls[-1]["timeout"]


class _GeminiBackend:
    name = "gemini"

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []
        self.translator = GeminiTranslator("en", "vi", api_key="key-test", model="gemini-test")
        models = SimpleNamespace(
            generate_content=self._generate_content,
            generate_content_stream=self._generate_content_stream,
        )
        self.translator.client = SimpleNamespace(models=models)

    def _generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(text="ok", candidates=[])

    def _generate_content_stream(self, **kwargs):
        self.calls.append(kwargs)
        return iter(
            [
                SimpleNamespace(text="ok", candidates=[]),
                SimpleNamespace(
                    text=None,
                    candidates=[SimpleNamespace(finish_reason=genai_types.FinishReason.STOP)],
                ),
            ]
        )

    def timeout_s(self) -> float:
        # The request's http_options is in milliseconds, unlike the other SDKs.
        return self.calls[-1]["config"].http_options.timeout / 1000


@pytest.fixture(params=[_OpenAIBackend, _AnthropicBackend, _GeminiBackend], ids=lambda cls: cls.name)
def backend(request, monkeypatch: pytest.MonkeyPatch):
    for module in (openai_translator, anthropic_translator, gemini_translator):
        monkeypatch.setattr(module, "_llm_cache_get", lambda *a, **k: None)
        monkeypatch.setattr(module, "_llm_cache_set", lambda *a, **k: None)
    return request.param()


# Streaming and plain requests are separate code paths in each backend, and each
# carried its own copy of the old constant.
@pytest.fixture(params=[False, True], ids=["plain", "streamed"])
def on_text(request):
    return (lambda offset, piece: None) if request.param else None


def test_a_short_prompt_is_sent_with_sixty_seconds(backend, on_text):
    backend.translator.generate(SHORT_PROMPT, system=SYSTEM, on_text=on_text)

    assert backend.timeout_s() == pytest.approx(60.0)


def test_a_whole_document_prompt_is_sent_with_the_computed_timeout(backend, on_text):
    backend.translator.generate(WHOLE_DOCUMENT, system=SYSTEM, on_text=on_text)

    assert backend.timeout_s() >= 180.0
    assert backend.timeout_s() == pytest.approx(base.generate_timeout_s(WHOLE_DOCUMENT, SYSTEM, None))


def test_the_timeout_counts_the_images_actually_passed(backend, on_text):
    images = [PageImage(data=b"x" * 100_000, label=f"Page {i}") for i in range(40)]

    backend.translator.generate(SHORT_PROMPT, images=images, on_text=on_text)

    assert backend.timeout_s() > 60.0
    assert backend.timeout_s() == pytest.approx(base.generate_timeout_s(SHORT_PROMPT, None, images))


def test_the_system_prompt_is_counted_not_only_the_question(backend, on_text):
    backend.translator.generate(SHORT_PROMPT, system="s" * 400_000, on_text=on_text)

    assert backend.timeout_s() >= 180.0


def test_gemini_sends_the_servers_deadline_with_the_request(on_text):
    """google-genai also sends the timeout as `X-Server-Timeout`, and keeps one
    already on the client's headers: a client that once made a 30 s request
    would otherwise tell the server 30 s on every later request."""
    backend = _GeminiBackend()

    backend.translator.generate(WHOLE_DOCUMENT, system=SYSTEM, on_text=on_text)

    seconds = base.generate_timeout_s(WHOLE_DOCUMENT, SYSTEM, None)
    headers = backend.calls[-1]["config"].http_options.headers
    assert headers == {"X-Server-Timeout": str(int(seconds))}


# --- translate() is unchanged -----------------------------------------------


def test_regression_guard_openai_translate_keeps_its_short_timeout(monkeypatch: pytest.MonkeyPatch):
    """A guard, not a red test: one paragraph is a tiny prompt, and a hung
    request there should still fail fast."""
    backend = _OpenAIBackend()
    monkeypatch.setattr(openai_translator, "_llm_cache_get", lambda *a, **k: None)
    monkeypatch.setattr(openai_translator, "_llm_cache_set", lambda *a, **k: None)

    backend.translator.translate("Hello world")

    assert backend.calls[0]["timeout"] == 30


def test_regression_guard_anthropic_translate_keeps_its_short_timeout(monkeypatch: pytest.MonkeyPatch):
    backend = _AnthropicBackend()
    monkeypatch.setattr(anthropic_translator, "_llm_cache_get", lambda *a, **k: None)
    monkeypatch.setattr(anthropic_translator, "_llm_cache_set", lambda *a, **k: None)

    backend.translator.translate("Hello world")

    assert backend.calls[0]["timeout"] == 30


def test_regression_guard_gemini_translate_leaves_the_client_wide_timeout(monkeypatch: pytest.MonkeyPatch):
    """The 30 s lives on the client; a per-request override on translate()
    would silently replace it."""
    backend = _GeminiBackend()
    monkeypatch.setattr(gemini_translator, "_llm_cache_get", lambda *a, **k: None)
    monkeypatch.setattr(gemini_translator, "_llm_cache_set", lambda *a, **k: None)
    content = SimpleNamespace()
    backend._generate_content = lambda **kw: (
        backend.calls.append(kw)
        or SimpleNamespace(
            text="xin chao",
            candidates=[SimpleNamespace(content=content, finish_reason=genai_types.FinishReason.STOP)],
        )
    )
    backend.translator.client.models.generate_content = backend._generate_content

    backend.translator.translate("Hello world")

    assert backend.calls[0]["config"].http_options is None
