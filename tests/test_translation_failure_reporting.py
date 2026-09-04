"""What happens when a paragraph can't be translated (#16).

BabelDOC's `ILTranslator` logs translator errors and continues, so nothing
downstream of `translate()` can tell a clean run from one where every
paragraph fell back to its source text. These are the three decisions that
close that gap, all of them pure:

* classifying an error as fatal (the credentials are wrong) vs transient;
* counting the failure and reporting it out of `BaseTranslator`;
* refusing to put a run with failures into the whole-PDF cache.

Deliberately stays out of `processors/processor.py` and
`api/routes/translation.py`, which drag in BabelDOC + torch — the same
trade-off `test_pdf_export_api.py` and `test_translate_language_contract.py`
document.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from desktop_pdf_translator.processors.pdf_cache import is_cacheable_artifact
from desktop_pdf_translator.translators.base import (
    BaseTranslator,
    is_fatal_translation_error,
)


# ---------------------------------------------------------------------------
# Fatal vs transient
# ---------------------------------------------------------------------------


class _SdkError(Exception):
    """Stand-in for an SDK exception carrying an explicit status."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class _Response:
    def __init__(self, status_code: int):
        self.status_code = status_code


class _WrappedError(Exception):
    """Some clients hang the status off a `.response` instead."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.response = _Response(status_code)


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_credentials_are_fatal(status):
    assert is_fatal_translation_error(_SdkError("nope", status)) is True
    assert is_fatal_translation_error(_WrappedError("nope", status)) is True


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_rate_limits_and_outages_are_not_fatal(status):
    """A 429 or a 5xx may well succeed on the next paragraph — aborting the
    whole job on one would be worse than the partial result."""
    assert is_fatal_translation_error(_SdkError("slow down", status)) is False


def test_network_errors_are_not_fatal():
    assert is_fatal_translation_error(ConnectionError("connection reset")) is False
    assert is_fatal_translation_error(TimeoutError("timed out")) is False


def test_message_fallback_covers_sdks_without_a_status():
    """google-genai's ClientError puts the code in its message, not an
    attribute — so the string is the only signal available."""
    assert is_fatal_translation_error(
        Exception("400 API key not valid. Please pass a valid API key.")
    )
    assert is_fatal_translation_error(Exception("AuthenticationError: bad key"))


def test_ordinary_failures_are_not_misread_as_fatal():
    assert is_fatal_translation_error(ValueError("unsupported language pair")) is False
    assert is_fatal_translation_error(RuntimeError("model returned no text")) is False


def test_a_boolean_attribute_is_not_a_status():
    """`getattr(error, "code", None)` finds `True` on some wrappers; `bool`
    is an `int` subclass, and `True == 1` must not be read as a status."""

    class _Flagged(Exception):
        code = True

    assert is_fatal_translation_error(_Flagged("something")) is False


# ---------------------------------------------------------------------------
# Counting and reporting
# ---------------------------------------------------------------------------


class _StubTranslator(BaseTranslator):
    """Minimal concrete translator — `_handle_translation_error` is the shared
    funnel every backend routes its `except` clause into."""

    def _setup_translator(self, **kwargs):
        pass

    def translate(self, text: str) -> str:  # pragma: no cover - not exercised
        return text


def test_a_failed_paragraph_is_counted_and_still_returns_source_text():
    """The fallback stays — BabelDOC ignores anything raised here, so raising
    would lose the text without stopping the run. The *count* is the fix."""
    translator = _StubTranslator(lang_in="en", lang_out="vi")
    assert translator.failed_translations == 0

    out = translator._handle_translation_error(RuntimeError("boom"), "Hello")

    assert out == "Hello"
    assert translator.failed_translations == 1


def test_failures_are_reported_with_their_severity():
    seen: list[tuple[str, bool]] = []
    translator = _StubTranslator(
        lang_in="en",
        lang_out="vi",
        on_translation_failed=lambda error, fatal: seen.append((str(error), fatal)),
    )

    translator._handle_translation_error(_SdkError("rate limited", 429), "a")
    translator._handle_translation_error(_SdkError("invalid key", 401), "b")

    assert [fatal for _, fatal in seen] == [False, True]
    assert translator.failed_translations == 2


def test_a_raising_callback_never_breaks_a_run():
    """Same contract as the paragraph ticker: a UI hook is not allowed to take
    the translation down with it."""

    def explode(_error, _fatal):
        raise RuntimeError("callback is broken")

    translator = _StubTranslator(
        lang_in="en", lang_out="vi", on_translation_failed=explode
    )
    assert translator._handle_translation_error(ValueError("x"), "text") == "text"
    assert translator.failed_translations == 1


def test_a_translator_without_the_callback_still_counts():
    translator = _StubTranslator(lang_in="en", lang_out="vi")
    translator._handle_translation_error(ValueError("x"), "text")
    assert translator.failed_translations == 1


# ---------------------------------------------------------------------------
# What may enter the PDF cache
# ---------------------------------------------------------------------------


def _artifact(temp_translate_dir: Path) -> Path:
    return temp_translate_dir / "paper_translated_v003.pdf"


def test_a_clean_run_is_cacheable(temp_translate_dir: Path):
    assert is_cacheable_artifact(
        _artifact(temp_translate_dir), temp_translate_dir, "paper", 0
    )


def test_a_run_with_failures_is_never_cached(temp_translate_dir: Path):
    """The regression this issue is about. Those paragraphs are source text,
    so the artifact is a partly-untranslated document that looks finished —
    and the cache key is the input's hash, so it would be served on every
    later open until someone thought to click Re-translate."""
    assert not is_cacheable_artifact(
        _artifact(temp_translate_dir), temp_translate_dir, "paper", 1
    )
    assert not is_cacheable_artifact(
        _artifact(temp_translate_dir), temp_translate_dir, "paper", 47
    )


def test_an_artifact_from_another_document_is_refused(temp_translate_dir: Path):
    """`_find_translated_file` falls back to the newest `*.pdf` in the output
    dir, which can be something else entirely."""
    stray = temp_translate_dir / "something_else.pdf"
    assert not is_cacheable_artifact(stray, temp_translate_dir, "paper", 0)


def test_an_artifact_outside_the_job_dir_is_refused(tmp_path: Path, temp_translate_dir: Path):
    elsewhere = tmp_path / "paper_translated_v001.pdf"
    assert not is_cacheable_artifact(elsewhere, temp_translate_dir, "paper", 0)


def test_a_missing_artifact_is_refused(temp_translate_dir: Path):
    assert not is_cacheable_artifact(None, temp_translate_dir, "paper", 0)
