"""
Translation service interfaces for desktop PDF translator.
"""

from .base import BaseTranslator
from .openai_translator import OpenAITranslator
from .gemini_translator import GeminiTranslator
from .anthropic_translator import AnthropicTranslator
from .argos_translator import ArgosTranslator
from .factory import TranslatorFactory
from .translation_cache import TranslationCache, get_translation_cache

__all__ = [
    "BaseTranslator",
    "OpenAITranslator",
    "GeminiTranslator",
    "AnthropicTranslator",
    "ArgosTranslator",
    "TranslatorFactory",
    "TranslationCache",
    "get_translation_cache",
]
