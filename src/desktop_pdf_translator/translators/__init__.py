"""Translation service interfaces for desktop PDF translator.

Only free-to-import names are re-exported. `TranslatorFactory` and the four
backends are not: they load the OpenAI, Anthropic and google-genai SDKs (~3 s),
and this file runs whenever anything imports a sibling module such as
`translators.capabilities`. Import from `translators.factory` at the call site.
"""

from .base import BaseTranslator
from .translation_cache import TranslationCache, get_translation_cache

__all__ = [
    "BaseTranslator",
    "TranslationCache",
    "get_translation_cache",
]
