"""
Translator factory for creating translator instances.
"""

import logging
from typing import Dict, Type, Optional, List

from ..config import TranslationService, get_settings
from .base import BaseTranslator
from .openai_translator import OpenAITranslator, OPENAI_AVAILABLE
from .gemini_translator import GeminiTranslator, GEMINI_AVAILABLE


logger = logging.getLogger(__name__)


class TranslatorFactory:
    """Factory for creating translator instances based on configuration."""
    
    _translators: Dict[TranslationService, Type[BaseTranslator]] = {
        TranslationService.OPENAI: OpenAITranslator,
        TranslationService.GEMINI: GeminiTranslator
    }
    
    @classmethod
    def create_translator(
        self, 
        service: Optional[TranslationService] = None,
        lang_in: Optional[str] = None, 
        lang_out: Optional[str] = None,
        **kwargs
    ) -> BaseTranslator:
        """
        Create a translator instance based on configuration.
        
        Args:
            service: Translation service to use (optional, uses config default)
            lang_in: Source language (optional, uses config default)
            lang_out: Target language (optional, uses config default)
            **kwargs: Additional translator-specific configuration
            
        Returns:
            Configured translator instance
            
        Raises:
            ValueError: If service is not supported or not available
            ImportError: If required dependencies are not installed
        """
        settings = get_settings()
        
        # Use provided service or fallback to config
        if service is None:
            service = settings.translation.preferred_service
        
        # Use provided languages or fallback to config
        if lang_in is None:
            lang_in = settings.translation.default_source_lang
        if lang_out is None:
            lang_out = settings.translation.default_target_lang
        
        # Check if service is supported
        if service not in self._translators:
            available_services = list(self._translators.keys())
            raise ValueError(f"Unsupported service: {service}. Available: {available_services}")
        
        # Check if service dependencies are available
        availability_map = {
            TranslationService.OPENAI: OPENAI_AVAILABLE,
            TranslationService.GEMINI: GEMINI_AVAILABLE
        }
        
        if not availability_map.get(service, False):
            service_names = {
                TranslationService.OPENAI: "OpenAI (pip install openai)",
                TranslationService.GEMINI: "Google AI (pip install google-generativeai)"
            }
            raise ImportError(f"Dependencies for {service_names[service]} are not installed")
        
        # Get service-specific configuration
        service_config = self._get_service_config(service, settings)
        service_config.update(kwargs)
        
        # Create translator instance
        translator_class = self._translators[service]
        translator = translator_class(
            lang_in=lang_in,
            lang_out=lang_out, 
            **service_config
        )
        
        logger.info(f"Created translator: {translator}")
        return translator
    
    @classmethod
    def _get_service_config(self, service: TranslationService, settings) -> Dict:
        """Get configuration for specific service."""
        if service == TranslationService.OPENAI:
            return {
                "api_key": settings.openai.api_key,
                "model": settings.openai.model,
                "temperature": settings.openai.temperature,
                "max_tokens": settings.openai.max_tokens,
                "base_url": settings.openai.base_url
            }
        elif service == TranslationService.GEMINI:
            return {
                "api_key": settings.gemini.api_key,
                "model": settings.gemini.model,
                "temperature": settings.gemini.temperature
            }
        else:
            return {}
    
    @classmethod
    def get_available_services(cls) -> List[TranslationService]:
        """Get list of available translation services."""
        available = []
        
        if OPENAI_AVAILABLE:
            available.append(TranslationService.OPENAI)
        if GEMINI_AVAILABLE:
            available.append(TranslationService.GEMINI)
            
        return available
    
    @classmethod
    def validate_service_availability(cls, service: TranslationService) -> tuple[bool, str]:
        """Validate if a service is available and properly configured."""
        try:
            # Check dependencies
            availability_map = {
                TranslationService.OPENAI: OPENAI_AVAILABLE,
                TranslationService.GEMINI: GEMINI_AVAILABLE
            }
            
            if not availability_map.get(service, False):
                return False, f"Dependencies for {service} are not installed"
            
            # Check configuration
            settings = get_settings()
            
            if service == TranslationService.OPENAI:
                if not settings.openai.api_key:
                    return False, "OpenAI API key is not configured"
            elif service == TranslationService.GEMINI:
                if not settings.gemini.api_key:
                    return False, "Gemini API key is not configured"
            
            # Create test translator to validate configuration
            translator = cls.create_translator(
                service=service,
                lang_in="en",
                lang_out="vi"
            )
            
            # Validate configuration
            is_valid, message = translator.validate_configuration()
            return is_valid, message
            
        except Exception as e:
            return False, f"Service validation failed: {str(e)}"
    
