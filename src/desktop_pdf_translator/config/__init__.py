"""
Configuration package for desktop PDF translator.
"""

from .models import (
    AppSettings,
    LanguageCode, 
    TranslationService,
    OpenAISettings,
    GeminiSettings,
    TranslationSettings,
    GUISettings,
    ProcessingSettings,
    FileMetadata
)
from .manager import ConfigManager, get_config_manager, get_settings

__all__ = [
    # Models
    "AppSettings",
    "LanguageCode",
    "TranslationService", 
    "OpenAISettings",
    "GeminiSettings",
    "TranslationSettings",
    "GUISettings", 
    "ProcessingSettings",
    "FileMetadata",
    
    # Manager
    "ConfigManager",
    "get_config_manager",
    "get_settings"
]