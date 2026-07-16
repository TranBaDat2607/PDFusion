"""
OpenAI translator implementation with Vietnamese optimization.
"""

import logging
from typing import Optional, List, Dict, Any

from openai import OpenAI

from .base import BaseTranslator, LANGUAGE_DISPLAY_NAMES
from .translation_cache import llm_cache_get as _llm_cache_get, llm_cache_set as _llm_cache_set


logger = logging.getLogger(__name__)


class OpenAITranslator(BaseTranslator):
    """
    OpenAI-based translator with Vietnamese language optimization.

    Supports GPT-3.5, GPT-4, and other OpenAI models with special handling
    for Vietnamese language translations.
    """

    def __init__(self, lang_in: str, lang_out: str, **kwargs):
        super().__init__(lang_in, lang_out, **kwargs)

    def _setup_translator(self, **kwargs):
        self.api_key = kwargs.get("api_key")
        if not self.api_key:
            raise ValueError("OpenAI API key is required")

        self.model = kwargs.get("model", "gpt-4")
        self.temperature = kwargs.get("temperature", 0.3)
        self.max_tokens = kwargs.get("max_tokens", 4000)
        self.base_url = kwargs.get("base_url")

        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

        logger.info(f"OpenAI translator configured with model: {self.model}")
    
    def translate(self, text: str, **kwargs) -> str:
        self.translate_call_count += 1

        try:
            processed_text = self._preprocess_text(text)
            if not processed_text.strip():
                return text

            cached = _llm_cache_get(processed_text, self.lang_in, self.lang_out, "openai", self.model)
            if cached is not None:
                self._fire_paragraph_callback(processed_text, cached)
                return cached

            response = self.client.chat.completions.create(
                model=self.model,
                messages=self._create_translation_prompt(processed_text),
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                timeout=30,
            )
            translated_text = response.choices[0].message.content.strip()
            result = self._postprocess_text(translated_text)
            _llm_cache_set(processed_text, result, self.lang_in, self.lang_out, "openai", self.model)
            self._fire_paragraph_callback(processed_text, result)
            return result

        except Exception as e:
            return self._handle_translation_error(e, text)

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 1000,
    ) -> Optional[str]:
        """Freeform generation used by the RAG chain."""
        try:
            messages: List[Dict[str, str]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.3,
                timeout=60,
            )
            content = response.choices[0].message.content
            return content.strip() if content else None
        except Exception as e:
            logger.error(f"OpenAI generate failed: {e}")
            return None

    def _create_translation_prompt(self, text: str) -> List[Dict[str, str]]:
        """Create optimized translation prompt for Vietnamese."""
        source_lang = LANGUAGE_DISPLAY_NAMES.get(self.lang_in, self.lang_in)
        target_lang = LANGUAGE_DISPLAY_NAMES.get(self.lang_out, self.lang_out)
        
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