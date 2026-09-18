"""The rough token count shown before an LLM run (#33).

The constants in `translators/usage_estimate.py` stand for the real prompts;
the last test holds them to those prompts, so lengthening a prompt fails here
rather than quietly understating every estimate.
"""

from __future__ import annotations

import pytest

from desktop_pdf_translator.translators import usage_estimate
from desktop_pdf_translator.translators.usage_estimate import (
    CHARS_PER_TOKEN,
    count_cjk,
    estimate_tokens,
    prompt_tokens,
    text_tokens,
)


def test_cjk_characters_are_counted_apart_from_latin_text():
    assert count_cjk("Hello world") == 0
    assert count_cjk("山の天気") == 4
    assert count_cjk("한국어 text") == 3
    assert count_cjk("Tiếng Việt có dấu") == 0


def test_latin_text_runs_several_characters_to_a_token():
    assert text_tokens(0, 400) == 400 // CHARS_PER_TOKEN
    assert text_tokens(0, 1) == 1


def test_cjk_text_runs_about_a_token_a_character():
    assert text_tokens(10, 0) == 10
    assert text_tokens(10, 8) == 12


def test_the_vietnamese_prompt_is_the_longer_one():
    assert prompt_tokens("vi") > prompt_tokens("ja")


def test_each_paragraph_carries_a_prompt():
    one = estimate_tokens(paragraphs=1, cjk_chars=0, other_chars=400, target_lang="vi")
    ten = estimate_tokens(paragraphs=10, cjk_chars=0, other_chars=400, target_lang="vi")
    assert ten.input_tokens - one.input_tokens == 9 * prompt_tokens("vi")
    assert ten.output_tokens == one.output_tokens


def test_a_translation_comes_back_longer_than_its_source():
    estimate = estimate_tokens(paragraphs=1, cjk_chars=0, other_chars=4000, target_lang="vi")
    assert estimate.output_tokens > text_tokens(0, 4000)


def test_nothing_to_translate_costs_nothing():
    estimate = estimate_tokens(paragraphs=0, cjk_chars=0, other_chars=0, target_lang="vi")
    assert (estimate.input_tokens, estimate.output_tokens) == (0, 0)


def _prompt_chars(translator_cls, target_lang: str) -> int:
    translator = translator_cls.__new__(translator_cls)
    translator.lang_in, translator.lang_out = "en", target_lang
    prompt = translator._create_translation_prompt("")
    if isinstance(prompt, list):  # OpenAI: chat messages
        return sum(len(message["content"]) for message in prompt)
    if isinstance(prompt, tuple):  # Anthropic: (system, user)
        return sum(len(part) for part in prompt)
    return len(prompt)  # Gemini: one string


@pytest.mark.parametrize("module, name", [
    ("openai_translator", "OpenAITranslator"),
    ("anthropic_translator", "AnthropicTranslator"),
    ("gemini_translator", "GeminiTranslator"),
])
@pytest.mark.parametrize("target_lang", ["vi", "ja"])
def test_the_prompt_constants_match_the_prompts(module, name, target_lang):
    import importlib

    cls = getattr(
        importlib.import_module(f"desktop_pdf_translator.translators.{module}"), name
    )
    measured = _prompt_chars(cls, target_lang) / usage_estimate.CHARS_PER_TOKEN
    assert prompt_tokens(target_lang) == pytest.approx(measured, rel=0.1)
