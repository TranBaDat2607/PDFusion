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


def _prompt_text(cls, lang_out: str, **kwargs) -> str:
    translator = cls(lang_in="en", lang_out=lang_out, api_key="test-key", **kwargs)
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


# ---------------------------------------------------------------------------
# the user's instructions
# ---------------------------------------------------------------------------

INSTRUCTIONS = "Keep programming terms such as index in English."
ALL_TRANSLATORS = [OpenAITranslator, AnthropicTranslator, GeminiTranslator]


@pytest.mark.parametrize("cls", ALL_TRANSLATORS)
def test_a_translator_exposes_the_instructions_it_was_given(cls):
    translator = cls(lang_in="en", lang_out="vi", api_key="test-key", instructions=INSTRUCTIONS)
    assert translator.instructions == INSTRUCTIONS


@pytest.mark.parametrize("cls", ALL_TRANSLATORS)
def test_a_translator_has_no_instructions_unless_given_some(cls):
    assert cls(lang_in="en", lang_out="vi", api_key="test-key").instructions is None


@pytest.mark.parametrize("cls", ALL_TRANSLATORS)
@pytest.mark.parametrize("lang_out", ["vi", "ja"])
def test_the_instructions_reach_the_prompt_inside_their_own_block(cls, lang_out: str):
    """The fence is what lets the fixed rules say they win: text between the
    tags is the user's, whatever it claims."""
    prompt = _prompt_text(cls, lang_out, instructions=INSTRUCTIONS)

    assert "<user-instructions>" in prompt and "</user-instructions>" in prompt
    block = prompt[prompt.index("<user-instructions>") : prompt.index("</user-instructions>")]
    assert INSTRUCTIONS in block


@pytest.mark.parametrize("cls", ALL_TRANSLATORS)
def test_the_instructions_come_after_the_fixed_rules(cls):
    """Last, so nothing the user wrote can sit between the model and the
    placeholder rule it must obey over them."""
    translator = cls(lang_in="en", lang_out="vi", api_key="test-key", instructions=INSTRUCTIONS)
    formula, _pattern = translator.get_formular_placeholder(1)

    prompt = _prompt_text(cls, "vi", instructions=INSTRUCTIONS)

    assert "<user-instructions>" in prompt
    assert prompt.rindex(formula) < prompt.index("<user-instructions>") < prompt.index(INSTRUCTIONS)


@pytest.mark.parametrize("cls", ALL_TRANSLATORS)
def test_no_instructions_leave_the_prompt_exactly_as_it_was(cls):
    assert _prompt_text(cls, "vi", instructions=None) == _prompt_text(cls, "vi")
    assert "user-instructions" not in _prompt_text(cls, "vi")


# sha256 of `_translation_instructions()` as it was before instructions
# existed, taken from that commit's code rather than from this one: "no
# instructions" must cost nothing, including a changed prompt for everyone.
PROMPT_BEFORE_INSTRUCTIONS = {
    "vi": "50cccc2a1b48dbde377068914165a7e8a11fe25f25339e7d7dba6eba925b62b2",
    "ja": "7f3a641dea34ad0c4e19968d72d76ed7dfa3332b7a6aaca70c0739902b09efa4",
}


@pytest.mark.parametrize("cls", ALL_TRANSLATORS)
@pytest.mark.parametrize("lang_out", ["vi", "ja"])
def test_no_instructions_send_the_prompt_sent_before_they_existed(cls, lang_out: str):
    import hashlib

    prompt = cls(lang_in="en", lang_out=lang_out, api_key="test-key")._translation_instructions()

    assert hashlib.sha256(prompt.encode()).hexdigest() == PROMPT_BEFORE_INSTRUCTIONS[lang_out]


@pytest.mark.parametrize("cls", ALL_TRANSLATORS)
@pytest.mark.parametrize("lang_out, language", [("vi", "Vietnamese"), ("ja", "Japanese")])
def test_the_instructions_are_told_which_rules_they_cannot_override(
    cls, lang_out: str, language: str
):
    """A user asking for notes, or for another language, would otherwise have
    them printed into the PDF; the lead-in before the fence says what wins."""
    translator = cls(lang_in="en", lang_out=lang_out, api_key="test-key", instructions=INSTRUCTIONS)
    formula, _pattern = translator.get_formular_placeholder(1)

    prompt = _prompt_text(cls, lang_out, instructions=INSTRUCTIONS)
    lead_in = prompt[prompt.rindex(formula) : prompt.index("<user-instructions>")]
    after_rules = lead_in.split("\n\n", 1)[1]

    assert language in after_rules
    assert "translation alone" in after_rules
    assert "placeholder" in after_rules
