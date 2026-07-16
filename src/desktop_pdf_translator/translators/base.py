"""
Base translator interface compatible with BabelDOC.
"""

import logging
import re
from abc import ABC, abstractmethod
from typing import Dict, Optional, Any

from ..config import LanguageCode


logger = logging.getLogger(__name__)


LANGUAGE_DISPLAY_NAMES: Dict[str, str] = {
    "vi": "Vietnamese (Tiếng Việt)",
    "en": "English",
    "ja": "Japanese (日本語)",
    "zh-cn": "Simplified Chinese (简体中文)",
    "zh-tw": "Traditional Chinese (繁體中文)",
    "auto": "automatically detected language",
}


class BaseTranslator(ABC):
    """
    Base translator interface compatible with BabelDOC integration.
    
    This interface follows the BabelDOC specification for translator compatibility:
    - Must implement translate() method accepting a single text string
    - Must support formula placeholder handling
    - Must support language attributes: lang_in, lang_out
    """

    def __init__(self, lang_in: str, lang_out: str, **kwargs):
        """Initialize translator with language configuration.

        Args:
            lang_in: Source language code
            lang_out: Target language code
            **kwargs: Additional translator-specific configuration. Recognized
                across all backends:
                  on_paragraph_translated: Optional[Callable[[str, str], None]]
                    Fired (from whatever thread translate() runs on) after
                    each paragraph is translated. Receives (source, target).
                    Used by the processor to emit `paragraph_translated`
                    SSE events for the live ticker UI.
        """
        self.lang_in = self._normalize_language_code(lang_in)
        self.lang_out = self._normalize_language_code(lang_out)
        self.translate_call_count = 0

        # Pop the cross-cutting callback before passing the rest to the
        # subclass setup so backends don't need to thread it through their
        # own kwargs handling.
        self._on_paragraph_translated = kwargs.pop(
            "on_paragraph_translated", None
        )

        # Initialize translator-specific settings
        self._setup_translator(**kwargs)

        logger.info(f"Initialized {self.__class__.__name__} translator: {self.lang_in} -> {self.lang_out}")

    def _fire_paragraph_callback(self, source: str, target: str) -> None:
        """Best-effort: invoke the on_paragraph_translated callback if set.
        Any exception in the callback is swallowed — we never want a UI hook
        to break a translation."""
        cb = self._on_paragraph_translated
        if cb is None:
            return
        try:
            cb(source, target)
        except Exception:
            logger.debug("on_paragraph_translated callback raised", exc_info=True)
    
    def _normalize_language_code(self, lang_code: str) -> str:
        """Normalize language code for translator compatibility."""
        # Map our enum values to common formats
        lang_map = {
            LanguageCode.VIETNAMESE: "vi",
            LanguageCode.ENGLISH: "en", 
            LanguageCode.JAPANESE: "ja",
            LanguageCode.CHINESE_SIMPLIFIED: "zh-cn",
            LanguageCode.CHINESE_TRADITIONAL: "zh-tw",
            LanguageCode.AUTO: "auto"
        }
        
        return lang_map.get(lang_code, lang_code)
    
    @abstractmethod
    def _setup_translator(self, **kwargs):
        """Setup translator-specific configuration."""
        pass
    
    @abstractmethod
    def translate(self, text: str) -> str:
        """
        Translate the given text.
        
        This is the main interface method that BabelDOC will call.
        Must accept a single text string and return a single translated string.
        
        Args:
            text: Text to translate
            
        Returns:
            Translated text
        """
        pass

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 1000,
    ) -> Optional[str]:
        """Freeform text generation for RAG answer synthesis.

        LLM backends override this. The base implementation returns None,
        meaning the backend (e.g. Argos NMT) cannot follow instructions —
        callers must fall back to a non-LLM path.
        """
        return None


    def get_formular_placeholder(self, placeholder_id: int) -> tuple[str, str]:
        """
        Get formula placeholder for protecting math content.
        
        BabelDOC uses this for formula preservation during translation.
        
        Args:
            placeholder_id: Unique identifier for the placeholder
            
        Returns:
            Tuple of (placeholder_text, regex_pattern)
        """
        placeholder = f"{{v{placeholder_id}}}"
        regex_pattern = f"{{\\s*v\\s*{placeholder_id}\\s*}}"
        return placeholder, regex_pattern
    
    def get_rich_text_left_placeholder(self, placeholder_id: int | str):
        return f"<b{placeholder_id}>"

    def get_rich_text_right_placeholder(self, placeholder_id: int | str):
        return f"</b{placeholder_id}>"
    
    def restore_formular_placeholder(self, text: str, placeholder_id: int, original_formula: str) -> str:
        """
        Restore formula placeholder with original content.
        
        Args:
            text: Text containing placeholder
            placeholder_id: Placeholder identifier
            original_formula: Original formula content to restore
            
        Returns:
            Text with restored formula
        """
        placeholder, regex_pattern = self.get_formular_placeholder(placeholder_id)
        # The formula is literal text, not a regex replacement template — a
        # lambda keeps backslashes/`\1` in it from being interpreted by re.sub.
        return re.sub(
            regex_pattern, lambda _m: original_formula, text, flags=re.IGNORECASE
        )

    def _preprocess_text(self, text: str) -> str:
        """Preprocess text before translation."""
        # Basic text cleaning
        text = text.strip()
        
        # Handle Vietnamese-specific preprocessing
        if self.lang_out == "vi":
            # Add Vietnamese-specific text normalization here if needed
            pass
        
        return text
    
    def _postprocess_text(self, text: str) -> str:
        """Postprocess translated text."""
        text = text.strip()

        if self.lang_out == "vi":
            text = re.sub(r'\s+([.,;:!?])', r'\1', text)
            # Insert a space after punctuation only at genuine word boundaries.
            # `,;:` → only when followed by a letter, so `1,000`, `12:30`,
            # and `http://` stay intact. Sentence enders `.!?` → only before
            # an uppercase letter (sentence boundary), so `3.14`,
            # `example.com`, and `?a=1` query strings stay intact.
            text = re.sub(r'([,;:])(?=[^\W\d_])', r'\1 ', text)
            text = re.sub(r'([.!?])(?=[A-Z])', r'\1 ', text)
            text = re.sub(r' {2,}', ' ', text)

        return text
    
    def _handle_translation_error(self, error: Exception, text: str) -> str:
        """Handle translation errors gracefully."""
        logger.error(f"Translation failed for text: {text[:100]}..., Error: {error}")
        
        # Return original text as fallback
        return text
    
    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.lang_in} -> {self.lang_out})"
    
    def __repr__(self) -> str:
        return self.__str__()