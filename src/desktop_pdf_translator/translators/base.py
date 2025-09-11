"""
Base translator interface compatible with BabelDOC.
"""

import logging
import re
from abc import ABC, abstractmethod
from typing import Dict, Optional, Any

from ..config import LanguageCode


logger = logging.getLogger(__name__)


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
            **kwargs: Additional translator-specific configuration
        """
        self.lang_in = self._normalize_language_code(lang_in)
        self.lang_out = self._normalize_language_code(lang_out)
        self.translate_call_count = 0
        
        # Initialize translator-specific settings
        self._setup_translator(**kwargs)
        
        logger.info(f"Initialized {self.__class__.__name__} translator: {self.lang_in} -> {self.lang_out}")
    
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
        return re.sub(regex_pattern, original_formula, text, flags=re.IGNORECASE)
    
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
        # Basic text cleaning
        text = text.strip()
        
        # Handle Vietnamese-specific postprocessing
        if self.lang_out == "vi":
            # Add Vietnamese-specific text formatting here if needed
            # For example, proper spacing around punctuation
            text = re.sub(r'\s+([.,;:!?])', r'\1', text)  # Remove space before punctuation
            text = re.sub(r'([.,;:!?])([^\s])', r'\1 \2', text)  # Add space after punctuation
        
        return text
    
    def _handle_translation_error(self, error: Exception, text: str) -> str:
        """Handle translation errors gracefully."""
        logger.error(f"Translation failed for text: {text[:100]}..., Error: {error}")
        
        # Return original text as fallback
        return text
    
    def get_translation_info(self) -> Dict[str, Any]:
        """Get translator information for debugging and monitoring."""
        return {
            "translator_class": self.__class__.__name__,
            "lang_in": self.lang_in,
            "lang_out": self.lang_out,
            "translate_call_count": self.translate_call_count
        }
    
    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.lang_in} -> {self.lang_out})"
    
    def __repr__(self) -> str:
        return self.__str__()