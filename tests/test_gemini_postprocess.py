"""Gemini wraps Vietnamese words in Markdown emphasis; the page has no use for it."""

from __future__ import annotations

import pytest

from desktop_pdf_translator.translators.gemini_translator import GeminiTranslator


@pytest.fixture
def translator() -> GeminiTranslator:
    return GeminiTranslator(lang_in="en", lang_out="vi", api_key="test-key")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Đây là **quan trọng** nhất", "Đây là quan trọng nhất"),
        ("Hãy *nhấn mạnh* điều này", "Hãy nhấn mạnh điều này"),
        ("Điều này ***rất*** quan trọng", "Điều này rất quan trọng"),
    ],
)
def test_markdown_emphasis_is_removed_and_its_words_kept(translator, text, expected):
    assert translator._postprocess_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Kết quả có ý nghĩa*",
        "Kết quả là 2 * 3 = 6",
        "x**2 + y**2",
        "{v1}*{v2}*{v3}",
    ],
)
def test_an_asterisk_that_is_not_emphasis_survives(translator, text):
    """A footnote marker, a product, an exponent and multiplied placeholders are content, not decoration."""
    assert translator._postprocess_text(text) == text
