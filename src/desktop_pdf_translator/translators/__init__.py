"""
Translation service interfaces for desktop PDF translator.
"""

from .base import BaseTranslator
from .openai_translator import OpenAITranslator
from .gemini_translator import GeminiTranslator
from .factory import TranslatorFactory

__all__ = [
    "BaseTranslator",
    "OpenAITranslator", 
    "GeminiTranslator",
    "TranslatorFactory"
]