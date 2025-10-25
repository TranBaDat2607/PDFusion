"""
Google Gemini translator implementation with Vietnamese optimization.
"""

import logging
import time
from typing import Optional, List, Dict, Any

try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    genai = None

from .base import BaseTranslator


logger = logging.getLogger(__name__)


class GeminiTranslator(BaseTranslator):
    """
    Google Gemini-based translator with Vietnamese language optimization.
    
    Supports Gemini Pro and other Google AI models with special handling
    for Vietnamese language translations.
    """
    
    def __init__(self, lang_in: str, lang_out: str, **kwargs):
        """Initialize Gemini translator."""
        if not GEMINI_AVAILABLE:
            raise ImportError("Google AI library is not installed. Please install with: pip install google-generativeai")
        
        super().__init__(lang_in, lang_out, **kwargs)
    
    def _setup_translator(self, **kwargs):
        """Setup Gemini client and configuration."""
        self.api_key = kwargs.get("api_key")
        if not self.api_key:
            raise ValueError("Gemini API key is required")
        
        self.model_name = kwargs.get("model", "gemini-pro")
        self.temperature = kwargs.get("temperature", 0.3)
        
        # Configure Gemini
        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel(self.model_name)
        
        # Configure generation settings
        self.generation_config = genai.types.GenerationConfig(
            temperature=self.temperature,
            max_output_tokens=4000,
            candidate_count=1
        )
        
        # Safety settings for content filtering
        self.safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        ]
        
        # Rate limiting
        self.last_request_time = 0
        self.min_request_interval = 2.0  # Minimum 2 seconds between requests for Gemini
        
        logger.info(f"Gemini translator configured with model: {self.model_name}")
    
    def translate(self, text: str, **kwargs) -> str:
        """
        Translate text using Google Gemini.
        
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
            
            # Call Gemini API
            response = self.model.generate_content(
                prompt,
                generation_config=self.generation_config,
                safety_settings=self.safety_settings
            )
            
            # Handle potential content filtering
            if response.candidates and response.candidates[0].content:
                translated_text = response.text.strip()
            else:
                # If content was filtered, return original text
                logger.warning("Gemini response was filtered or empty")
                return text
            
            # Postprocess and return
            return self._postprocess_text(translated_text)
            
        except Exception as e:
            return self._handle_translation_error(e, text)
    
    def _create_translation_prompt(self, text: str) -> str:
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
    
    def _apply_rate_limiting(self):
        """Apply rate limiting for Gemini API."""
        current_time = time.time()
        time_since_last_request = current_time - self.last_request_time
        
        if time_since_last_request < self.min_request_interval:
            sleep_time = self.min_request_interval - time_since_last_request
            time.sleep(sleep_time)
        
        self.last_request_time = time.time()
    
    def _postprocess_text(self, text: str) -> str:
        """Vietnamese-specific postprocessing for Gemini."""
        text = super()._postprocess_text(text)
        
        if self.lang_out == "vi":
            # Vietnamese-specific formatting for Gemini output
            # Remove common Gemini artifacts
            text = text.replace("**", "")  # Remove bold markdown
            text = text.replace("*", "")   # Remove italic markdown
            
            # Fix Vietnamese punctuation
            text = text.replace(" ,", ",")
            text = text.replace(" .", ".")
            text = text.replace(" ;", ";")
            text = text.replace(" :", ":")
            text = text.replace(" !", "!")
            text = text.replace(" ?", "?")
            
            # Normalize Vietnamese quotes
            text = text.replace(""", '"').replace(""", '"')
            text = text.replace("'", "'").replace("'", "'")
        
        return text
    
    def validate_configuration(self) -> tuple[bool, str]:
        """Validate Gemini configuration."""
        try:
            if not self.api_key:
                return False, "API key is missing"
            
            # Test API connection with a minimal request
            response = self.model.generate_content(
                "Hello",
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=5,
                    temperature=0
                )
            )
            
            if response.text:
                return True, "Configuration is valid"
            else:
                return False, "Invalid API response"
                
        except Exception as e:
            return False, f"Configuration error: {str(e)}"