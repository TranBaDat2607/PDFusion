"""What every LLM translator's prompt tells the model about its input.

BabelDOC hands `translate()` paragraphs with its placeholders already in them
— the ones `BaseTranslator.get_formular_placeholder` and the rich-text pair
produce — because PDFusion's translators implement no `do_llm_translate`, so
BabelDOC's own prompt (which explains them) is never used. A model told only
"keep formulas intact" has no way to know `{v1}` is one.
"""

from __future__ import annotations

import re

import pytest

from desktop_pdf_translator.translators.anthropic_translator import AnthropicTranslator
from desktop_pdf_translator.translators.gemini_translator import GeminiTranslator
from desktop_pdf_translator.translators.openai_translator import OpenAITranslator


def _prompt_text(cls, lang_out: str) -> str:
    translator = cls(lang_in="en", lang_out=lang_out, api_key="test-key")
    prompt = translator._create_translation_prompt("Hello")
    if isinstance(prompt, str):
        return prompt
    if isinstance(prompt, tuple):
        return "\n".join(prompt)
    return "\n".join(message["content"] for message in prompt)


@pytest.mark.parametrize("cls", [OpenAITranslator, AnthropicTranslator, GeminiTranslator])
@pytest.mark.parametrize("lang_out", ["vi", "ja"])
def test_the_prompt_names_the_placeholders_the_translator_issues(cls, lang_out: str):
    translator = cls(lang_in="en", lang_out=lang_out, api_key="test-key")
    formula, _pattern = translator.get_formular_placeholder(1)
    left, _left_pattern = translator.get_rich_text_left_placeholder(0)
    right, _right_pattern = translator.get_rich_text_right_placeholder(0)

    prompt = _prompt_text(cls, lang_out)

    assert formula in prompt
    assert left in prompt and right in prompt


def _protocol_translators():
    translators = [OpenAITranslator(lang_in="en", lang_out="vi", api_key="test-key")]
    try:
        from desktop_pdf_translator.translators.argos_translator import ArgosTranslator

        translators.append(ArgosTranslator(lang_in="en", lang_out="vi"))
    except Exception:
        pass
    return translators


@pytest.mark.parametrize("side", ["left", "right"])
@pytest.mark.parametrize("index", [0, 1])
def test_rich_text_placeholders_follow_babeldocs_protocol(side: str, index: int):
    """BabelDOC compiles `result[1]` as the regex for placeholder-like tokens a
    model may have invented and deletes every match that isn't a real one; a
    bare string there was unpacked into characters, so "b" and "/" were
    stripped from every translated paragraph."""
    translator = _protocol_translators()[index]
    make = getattr(translator, f"get_rich_text_{side}_placeholder")

    result = make(0)
    assert isinstance(result, tuple) and len(result) == 2
    placeholder, _ = result
    assert isinstance(placeholder, str) and "0" in placeholder

    pattern = make(r"\d+")[1]
    assert re.fullmatch(pattern, make(7)[0], re.I)
    assert re.search(pattern, "Bảng b/c và sub/dir", re.I) is None
