"""
Google Gemini translator implementation with Vietnamese optimization.
"""

import logging
from typing import Optional, List, Dict, Any

from google import genai
from google.genai import types as genai_types

from .base import BaseTranslator, LANGUAGE_DISPLAY_NAMES
from .translation_cache import llm_cache_get as _llm_cache_get, llm_cache_set as _llm_cache_set


logger = logging.getLogger(__name__)


class GeminiTranslator(BaseTranslator):
    """
    Google Gemini-based translator with Vietnamese language optimization.

    Supports Gemini Pro and other Google AI models with special handling
    for Vietnamese language translations.
    """

    min_request_interval = 2.0

    def __init__(self, lang_in: str, lang_out: str, **kwargs):
        super().__init__(lang_in, lang_out, **kwargs)

    def _setup_translator(self, **kwargs):
        self.api_key = kwargs.get("api_key")
        if not self.api_key:
            raise ValueError("Gemini API key is required")

        self.model_name = kwargs.get("model", "gemini-pro")
        self.temperature = kwargs.get("temperature", 0.3)

        self.client = genai.Client(api_key=self.api_key)
        self.generation_config = genai_types.GenerateContentConfig(
            temperature=self.temperature,
            max_output_tokens=4000,
            candidate_count=1,
            safety_settings=[
                genai_types.SafetySetting(category="HARM_CATEGORY_HARASSMENT",        threshold="BLOCK_MEDIUM_AND_ABOVE"),
                genai_types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH",       threshold="BLOCK_MEDIUM_AND_ABOVE"),
                genai_types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_MEDIUM_AND_ABOVE"),
                genai_types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_MEDIUM_AND_ABOVE"),
            ],
        )

        logger.info(f"Gemini translator configured with model: {self.model_name}")
    
    def translate(self, text: str, **kwargs) -> str:
        self.translate_call_count += 1

        try:
            processed_text = self._preprocess_text(text)
            if not processed_text.strip():
                return text

            cached = _llm_cache_get(processed_text, self.lang_in, self.lang_out, "gemini", self.model_name)
            if cached is not None:
                self._fire_paragraph_callback(processed_text, cached)
                return cached

            self._apply_rate_limiting()

            response = self.client.models.generate_content(
                model=self.model_name,
                contents=self._create_translation_prompt(processed_text),
                config=self.generation_config,
            )

            if response.candidates and response.candidates[0].content:
                translated_text = response.text.strip()
            else:
                logger.warning("Gemini response was filtered or empty")
                return text

            result = self._postprocess_text(translated_text)
            _llm_cache_set(processed_text, result, self.lang_in, self.lang_out, "gemini", self.model_name)
            self._fire_paragraph_callback(processed_text, result)
            return result

        except Exception as e:
            return self._handle_translation_error(e, text)

    def _create_translation_prompt(self, text: str) -> str:
        """Create optimized translation prompt for Vietnamese."""
        source_lang = LANGUAGE_DISPLAY_NAMES.get(self.lang_in, self.lang_in)
        target_lang = LANGUAGE_DISPLAY_NAMES.get(self.lang_out, self.lang_out)
        
        # Create prompt with Vietnamese optimization
        if self.lang_out == "vi":
            prompt = f"""You are an expert Vietnamese translator. Translate the following text from {source_lang} to {target_lang}.

Guidelines for Vietnamese translation:
1. Use natural, fluent Vietnamese that sounds native
2. Apply appropriate Vietnamese grammar and sentence structure
3. Use proper Vietnamese honorifics (anh/chị/em) when contextually appropriate
4. Maintain technical accuracy for specialized terms
5. Preserve mathematical formulas, equations, and code exactly as written
6. Keep the original tone and formality level
7. Use correct Vietnamese punctuation and spacing
8. For academic content, prioritize clarity and precision

Text to translate:
{text}

Provide only the Vietnamese translation without any explanations or notes:"""
        else:
            prompt = f"""You are a professional translator. Translate the following text from {source_lang} to {target_lang}.

Requirements:
1. Maintain the original meaning and context exactly
2. Use natural, fluent language in {target_lang}
3. Preserve all mathematical formulas, equations, and code exactly
4. Keep technical terms accurate
5. Maintain the same formality level as the source
6. Do not add explanations or commentary

Text to translate:
{text}

Provide only the translation:"""
        
        return prompt
    
    def _postprocess_text(self, text: str) -> str:
        if self.lang_out == "vi":
            # Strip Gemini's markdown decorations before delegating to the shared
            # punctuation/quote cleanup.
            text = text.replace("**", "").replace("*", "")
        return super()._postprocess_text(text)

    def validate_configuration(self) -> tuple[bool, str]:
        """Validate Gemini configuration."""
        try:
            if not self.api_key:
                return False, "API key is missing"
            
            # Test API connection with a minimal request
            response = self.client.models.generate_content(
                model=self.model_name,
                contents="Hello",
                config=genai_types.GenerateContentConfig(
                    max_output_tokens=5,
                    temperature=0,
                ),
            )
            
            if response.text:
                return True, "Configuration is valid"
            else:
                return False, "Invalid API response"
                
        except Exception as e:
            return False, f"Configuration error: {str(e)}"