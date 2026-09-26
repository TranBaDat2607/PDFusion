"""
Configuration package for desktop PDF translator.
"""

from .models import (
    AppSettings,
    LanguageCode,
    TranslationService,
    ModelRef,
    ProviderSettings,
    TranslationSettings,
    GUISettings,
    ProcessingSettings,
    RAGSettings,
    FileMetadata
)
from .manager import ConfigManager, get_config_manager, get_settings

__all__ = [
    # Models
    "AppSettings",
    "LanguageCode",
    "TranslationService",
    "ModelRef",
    "ProviderSettings",
    "TranslationSettings",
    "GUISettings",
    "ProcessingSettings",
    "RAGSettings",
    "FileMetadata",

    # Manager
    "ConfigManager",
    "get_config_manager",
    "get_settings"
]