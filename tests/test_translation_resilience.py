"""Shared rate limiting, 429/5xx backoff, and cooperative cancel (#22).

Stays free of BabelDOC/processor imports, same trade-off as
test_translation_failure_reporting.py.
"""

from __future__ import annotations

import threading
import time

import pytest

from desktop_pdf_translator.translators import base
from desktop_pdf_translator.translators.base import (
    BaseTranslator,
    TranslationCancelled,
    is_retryable_translation_error,
)
from desktop_pdf_translator.translators.rate_limiter import (
    TokenBucketRateLimiter,
    get_rate_limiter,
)


class _SdkError(Exception):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# is_retryable_translation_error
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 529])
def test_rate_limits_and_outages_are_retryable(status):
    assert is_retryable_translation_error(_SdkError("x", status)) is True


@pytest.mark.parametrize("status", [401, 403, 400, 404])
def test_client_errors_are_not_retryable(status):
    assert is_retryable_translation_error(_SdkError("x", status)) is False


def test_errors_without_a_status_are_not_retryable():
    assert is_retryable_translation_error(ValueError("boom")) is False


# ---------------------------------------------------------------------------
# TokenBucketRateLimiter
# ---------------------------------------------------------------------------


def test_acquire_throttles_to_the_configured_rate():
    limiter = TokenBucketRateLimiter(rate=20.0, capacity=1)
    limiter.acquire()
    start = time.monotonic()
    limiter.acquire()
    assert time.monotonic() - start >= 0.04


def test_acquire_returns_false_when_cancelled_while_waiting():
    limiter = TokenBucketRateLimiter(rate=1.0, capacity=1)
    limiter.acquire()
    cancel = threading.Event()
    threading.Timer(0.05, cancel.set).start()
    assert limiter.acquire(cancel_event=cancel) is False


def test_get_rate_limiter_is_a_singleton_per_service():
    assert get_rate_limiter("resilience-test-a") is get_rate_limiter("resilience-test-a")
    assert get_rate_limiter("resilience-test-b") is not get_rate_limiter("resilience-test-c")


def test_unknown_services_get_the_fallback_default():
    limiter = get_rate_limiter("resilience-unknown-service")
    assert limiter._rate == 4.0


def test_an_explicit_qps_updates_an_existing_limiter_in_place():
    """A settings change must reach the *next* job without a sidecar
    restart — the qps argument is a no-op past construction otherwise."""
    limiter = get_rate_limiter("resilience-test-override", qps=2.0)
    assert limiter._rate == 2.0
    same = get_rate_limiter("resilience-test-override", qps=9.0)
    assert same is limiter
    assert limiter._rate == 9.0


def test_set_rate_never_exceeds_the_new_capacity():
    limiter = TokenBucketRateLimiter(rate=10.0, capacity=10.0)
    limiter.set_rate(1.0)
    assert limiter._tokens <= 1.0


# ---------------------------------------------------------------------------
# BaseTranslator cancellation + backoff plumbing
# ---------------------------------------------------------------------------


class _ResilientStub(BaseTranslator):
    def _setup_translator(self, **kwargs):
        self._service = kwargs.get("service", "resilience-stub")

    def translate(self, text: str) -> str:
        self._note_translate_call()
        if self.is_cancelled():
            return text
        try:
            return self._call_with_backoff(self._service, self._request)
        except TranslationCancelled:
            return text
        except Exception as e:
            return self._handle_translation_error(e, text)

    def _request(self):
        raise NotImplementedError


def test_translate_returns_source_text_when_already_cancelled():
    cancel = threading.Event()
    cancel.set()
    translator = _ResilientStub(
        lang_in="en", lang_out="vi", service="resilience-precancel", cancel_event=cancel
    )
    calls = []
    translator._request = lambda: calls.append(1)
    assert translator.translate("hello") == "hello"
    assert calls == []


def test_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setattr(base, "_RETRY_BASE_DELAY_S", 0.001)
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise _SdkError("slow down", 429)
        return "ok"

    translator = _ResilientStub(lang_in="en", lang_out="vi", service="resilience-retry-ok")
    translator._request = flaky
    assert translator.translate("hello") == "ok"
    assert attempts["n"] == 3
    assert translator.retry_count == 2


def test_fatal_errors_are_not_retried():
    attempts = {"n": 0}

    def unauthorized():
        attempts["n"] += 1
        raise _SdkError("bad key", 401)

    translator = _ResilientStub(lang_in="en", lang_out="vi", service="resilience-fatal")
    translator._request = unauthorized
    result = translator.translate("hello")
    assert result == "hello"
    assert attempts["n"] == 1
    assert translator.failed_translations == 1
    assert translator.retry_count == 0


def test_cancelling_mid_backoff_stops_retrying(monkeypatch):
    monkeypatch.setattr(base, "_RETRY_BASE_DELAY_S", 0.05)
    cancel = threading.Event()
    attempts = {"n": 0}

    def always_429():
        attempts["n"] += 1
        if attempts["n"] == 1:
            threading.Timer(0.01, cancel.set).start()
        raise _SdkError("slow down", 429)

    translator = _ResilientStub(
        lang_in="en", lang_out="vi", service="resilience-cancel-backoff", cancel_event=cancel
    )
    translator._request = always_429
    assert translator.translate("hello") == "hello"
    assert attempts["n"] == 1


# ---------------------------------------------------------------------------
# SDK clients must not retry on their own — their attempts would bypass the
# shared rate limiter entirely.
# ---------------------------------------------------------------------------


def test_openai_client_disables_its_own_retries():
    from desktop_pdf_translator.translators.openai_translator import OpenAITranslator

    translator = OpenAITranslator(lang_in="en", lang_out="vi", api_key="sk-test")
    assert translator.client.max_retries == 0


def test_anthropic_client_disables_its_own_retries():
    from desktop_pdf_translator.translators.anthropic_translator import AnthropicTranslator

    translator = AnthropicTranslator(lang_in="en", lang_out="vi", api_key="sk-ant-test")
    assert translator.client.max_retries == 0


def test_max_qps_is_read_from_kwargs():
    from desktop_pdf_translator.translators.openai_translator import OpenAITranslator

    translator = OpenAITranslator(
        lang_in="en", lang_out="vi", api_key="sk-test", max_qps=12.5
    )
    assert translator.max_qps == 12.5


# ---------------------------------------------------------------------------
# CompletionEvent surfaces retry_count (#22's third checklist item)
# ---------------------------------------------------------------------------


def test_completion_event_carries_the_retry_count():
    import time

    from desktop_pdf_translator.processors.events import CompletionEvent, EventType

    event = CompletionEvent(
        type=EventType.FINISH,
        timestamp=time.time(),
        session_id="s",
        data={},
        success=True,
        retry_count=3,
    )
    assert event.retry_count == 3
    assert event.data["retry_count"] == 3
