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

# The instructions every paragraph is sent with: the system prompt in
# `_create_translation_prompt` (OpenAI, Anthropic and Gemini are within a few
# characters of each other) plus the "Translate this text:" lead-in, at
# CHARS_PER_TOKEN. The Vietnamese prompt is the longer one.
# `tests/test_usage_estimate.py` holds these to the real prompts.
PROMPT_TOKENS_VIETNAMESE = 178
PROMPT_TOKENS_OTHER = 113

# A translation takes more tokens than its source: o200k counts 1.3x for an
# English paragraph in Vietnamese, 1.4x in Japanese.
OUTPUT_RATIO = 1.3


def count_cjk(text: str) -> int:
    return len(_CJK.findall(text))


def text_tokens(cjk_chars: int, other_chars: int) -> int:
    return cjk_chars + math.ceil(other_chars / CHARS_PER_TOKEN)


def prompt_tokens(target_lang: str) -> int:
    return PROMPT_TOKENS_VIETNAMESE if target_lang == "vi" else PROMPT_TOKENS_OTHER


@dataclass(frozen=True)
class TokenEstimate:
    input_tokens: int
    output_tokens: int


def estimate_tokens(
    *, paragraphs: int, cjk_chars: int, other_chars: int, target_lang: str
) -> TokenEstimate:
    """Tokens sent (text plus one prompt per paragraph) and received."""
    text = text_tokens(cjk_chars, other_chars)
    return TokenEstimate(
        input_tokens=text + paragraphs * prompt_tokens(target_lang),
        output_tokens=math.ceil(text * OUTPUT_RATIO),
    )
