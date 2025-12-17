"""
OpenAI translator implementation with Vietnamese optimization.
"""

import logging
import time
from typing import Optional, List, Dict, Any

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    OpenAI = None

from .base import BaseTranslator


logger = logging.getLogger(__name__)


class OpenAITranslator(BaseTranslator):
    """
    OpenAI-based translator with Vietnamese language optimization.
    
    Supports GPT-3.5, GPT-4, and other OpenAI models with special handling
    for Vietnamese language translations.
    """
    
    def __init__(self, lang_in: str, lang_out: str, **kwargs):
        """Initialize OpenAI translator."""
        if not OPENAI_AVAILABLE:
            raise ImportError("OpenAI library is not installed. Please install with: pip install openai")
        
        super().__init__(lang_in, lang_out, **kwargs)
    
    def _setup_translator(self, **kwargs):
        """Setup OpenAI client and configuration."""
        self.api_key = kwargs.get("api_key")
        if not self.api_key:
            raise ValueError("OpenAI API key is required")
        
        self.model = kwargs.get("model", "gpt-4")
        self.temperature = kwargs.get("temperature", 0.3)
        self.max_tokens = kwargs.get("max_tokens", 4000)
        self.base_url = kwargs.get("base_url")
        
        # Initialize OpenAI client
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )
        
        # Rate limiting
        self.last_request_time = 0
        self.min_request_interval = 1.0  # Minimum 1 second between requests
        
        logger.info(f"OpenAI translator configured with model: {self.model}")
    
    def translate(self, text: str, **kwargs) -> str:
        """
        Translate text using OpenAI GPT models.
        
        Args:
            text: Text to translate
            **kwargs: Additional translation parameters
            
        Returns:
            Translated text
        """
        self.translate_call_count += 1
        
        try:
            # Preprocess text
            processed_text = self._preprocess_text(text)
            if not processed_text.strip():
                return text
            
            # Rate limiting
            self._apply_rate_limiting()
            
            # Create translation prompt
            prompt = self._create_translation_prompt(processed_text)
            
            # Call OpenAI API
            response = self.client.chat.completions.create(
                model=self.model,
                messages=prompt,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                timeout=30
            )
            
            # Extract translation
            translated_text = response.choices[0].message.content.strip()
            
            # Postprocess and return
            return self._postprocess_text(translated_text)
            
        except Exception as e:
            return self._handle_translation_error(e, text)
    
    def _create_translation_prompt(self, text: str) -> List[Dict[str, str]]:
        """Create optimized translation prompt for Vietnamese."""
        
        # Get language names for prompt
        lang_names = {
            "vi": "Vietnamese (Tiếng Việt)",
            "en": "English", 
            "ja": "Japanese (日本語)",
            "zh-cn": "Simplified Chinese (简体中文)",
            "zh-tw": "Traditional Chinese (繁體中文)",
            "auto": "automatically detected language"
        }
        
        source_lang = lang_names.get(self.lang_in, self.lang_in)
        target_lang = lang_names.get(self.lang_out, self.lang_out)
        
        # Create system prompt with Vietnamese optimization
        if self.lang_out == "vi":
            system_prompt = f"""You are a professional translator specializing in Vietnamese language.
Translate the given text from {source_lang} to {target_lang} with the following guidelines:

1. Maintain natural Vietnamese syntax and grammar
2. Use appropriate Vietnamese honorifics and formality levels
3. Preserve technical terms when appropriate, but provide Vietnamese equivalents when possible
4. Keep mathematical formulas, code, and special formatting intact
5. Maintain the original meaning and tone
6. Use proper Vietnamese punctuation and spacing rules
7. For academic or technical content, prioritize clarity and accuracy

Translate ONLY the text content. Do not add explanations, notes, or commentary."""
        else:
            system_prompt = f"""You are a professional translator.
Translate the given text from {source_lang} to {target_lang} while:

1. Maintaining the original meaning and context
2. Preserving mathematical formulas, code, and special formatting
3. Using natural, fluent language in the target language
4. Keeping technical terms accurate
5. Maintaining appropriate formality level

Translate ONLY the text content. Do not add explanations, notes, or commentary."""
        
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Translate this text:\n\n{text}"}
        ]
    
    def _apply_rate_limiting(self):
        """Apply rate limiting to avoid API quota issues."""
        current_time = time.time()
        time_since_last_request = current_time - self.last_request_time
        
        if time_since_last_request < self.min_request_interval:
            sleep_time = self.min_request_interval - time_since_last_request
            time.sleep(sleep_time)
        
        self.last_request_time = time.time()
    
    def _postprocess_text(self, text: str) -> str:
        """Vietnamese-specific postprocessing."""
        text = super()._postprocess_text(text)
        
        if self.lang_out == "vi":
            # Vietnamese-specific formatting
            # Fix common translation artifacts
            text = text.replace("  ", " ")  # Remove double spaces
            text = text.replace(" ,", ",")  # Fix comma spacing
            text = text.replace(" .", ".")  # Fix period spacing
            
            # Handle Vietnamese quotation marks
            text = text.replace('"', '"').replace('"', '"')  # Normalize quotes
        
        return text
    
    def validate_configuration(self) -> tuple[bool, str]:
        """Validate OpenAI configuration."""
        try:
            if not self.api_key:
                return False, "API key is missing"
            
            # Test API connection with a minimal request
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "Hello"}],
                max_tokens=5,
                timeout=10
            )
            
            if response.choices:
                return True, "Configuration is valid"
            else:
                return False, "Invalid API response"
                
        except Exception as e:
            return False, f"Configuration error: {str(e)}"