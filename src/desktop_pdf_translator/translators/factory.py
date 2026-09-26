"""
Translator factory for creating translator instances.
"""

import logging
from typing import Dict, Optional

from ..config import TranslationService, get_settings
from ..providers.registry import provider
from .base import BaseTranslator


logger = logging.getLogger(__name__)


class TranslatorFactory:
    """Factory for creating translator instances based on configuration."""

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

        # Raises ValueError for a service no provider has. The class is
        # imported here, on first use, not at the top: each backend loads its
        # SDK, and this module used to pay for all four the moment anything
        # imported it.
        translator_class = provider(TranslationService(service).value).translator()

        # Get service-specific configuration
        service_config = self._get_service_config(service, settings)
        service_config.update(kwargs)

        # Create translator instance
        translator = translator_class(
            lang_in=lang_in,
            lang_out=lang_out,
            **service_config
        )

        logger.info(f"Created translator: {translator}")
        return translator

    @classmethod
    def _get_service_config(self, service: TranslationService, settings) -> Dict:
        """Get configuration for specific service.

        A service's settings section is exactly the keyword arguments its
        translator takes, so the whole section goes.
        """
        return getattr(settings, TranslationService(service).value).model_dump()
