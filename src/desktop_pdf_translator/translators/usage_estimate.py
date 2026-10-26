"""A rough count of the tokens an LLM translation will use (#33).

Shown before a run, so a user can tell a 5-page job from a 500-page one
before paying for it. Rough on purpose: it counts the PDF's text with
PyMuPDF, while BabelDOC finds its own paragraphs, and every provider has its
own tokenizer. Each constant below errs high.

Standard library only; `POST /translate/estimate` imports it at boot.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# Latin-script text, per token. OpenAI's o200k tokenizer averages about 5
# characters per token on English prose; 4 errs high.
CHARS_PER_TOKEN = 4

# Han, kana and Hangul run close to one token per character (o200k: 42
# Japanese characters, 36 tokens).
_CJK = re.compile(
    "[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯ｦ-ﾟ]"
)

# The instructions every paragraph is sent with: the text
# `BaseTranslator._translation_instructions` gives OpenAI, Anthropic and Gemini
# alike, plus the "Translate this text:" lead-in, at CHARS_PER_TOKEN. The
# Vietnamese prompt is the longer one.
# `tests/test_usage_estimate.py` holds these to the real prompts.
PROMPT_TOKENS_VIETNAMESE = 197
PROMPT_TOKENS_OTHER = 179

# A translation takes more tokens than its source: o200k counts 1.3x for an
# English paragraph in Vietnamese, 1.4x in Japanese.
OUTPUT_RATIO = 1.3


def count_cjk(text: str) -> int:
    return len(_CJK.findall(text))


def text_tokens(cjk_chars: int, other_chars: int) -> int:
    return cjk_chars + math.ceil(other_chars / CHARS_PER_TOKEN)


# The lead-in and fence `BaseTranslator._user_instructions_block` puts around
# the user's instructions, at CHARS_PER_TOKEN: 300 characters as measured for
# Vietnamese, the target it names in full.
INSTRUCTIONS_WRAPPER_TOKENS = 75


def prompt_tokens(target_lang: str, instructions: str | None = None) -> int:
    fixed = PROMPT_TOKENS_VIETNAMESE if target_lang == "vi" else PROMPT_TOKENS_OTHER
    if not instructions:
        return fixed
    return fixed + INSTRUCTIONS_WRAPPER_TOKENS + text_tokens(
        count_cjk(instructions), len(instructions) - count_cjk(instructions)
    )


@dataclass(frozen=True)
class TokenEstimate:
    input_tokens: int
    output_tokens: int


def estimate_tokens(
    *,
    paragraphs: int,
    cjk_chars: int,
    other_chars: int,
    target_lang: str,
    instructions: str | None = None,
) -> TokenEstimate:
    """Tokens sent (text plus one prompt per paragraph, the user's
    instructions included) and received."""
    text = text_tokens(cjk_chars, other_chars)
    return TokenEstimate(
        input_tokens=text + paragraphs * prompt_tokens(target_lang, instructions),
        output_tokens=math.ceil(text * OUTPUT_RATIO),
    )
