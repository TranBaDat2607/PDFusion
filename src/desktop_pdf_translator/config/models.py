"""
Configuration models for desktop PDF translator application.
"""

from enum import Enum
from pathlib import Path
from typing import Optional, Literal

from pydantic import BaseModel, Field, validator


class LanguageCode(str, Enum):
    """Supported language codes with Vietnamese priority."""
    
    AUTO = "auto"
    VIETNAMESE = "vi"
    ENGLISH = "en"
    JAPANESE = "ja"
    CHINESE_SIMPLIFIED = "zh-cn"
    CHINESE_TRADITIONAL = "zh-tw"


class TranslationService(str, Enum):
    """Supported translation services."""
    
    OPENAI = "openai"
    GEMINI = "gemini"


class OpenAISettings(BaseModel):
    """OpenAI translation service settings."""
    
    api_key: Optional[str] = Field(None, description="OpenAI API key")
    model: str = Field("gpt-4", description="OpenAI model to use")
    base_url: Optional[str] = Field(None, description="Custom API base URL")
    temperature: float = Field(0.3, ge=0.0, le=2.0, description="Translation creativity")
    max_tokens: Optional[int] = Field(None, description="Maximum tokens per request")


class GeminiSettings(BaseModel):
    """Google Gemini translation service settings."""
    
    api_key: Optional[str] = Field(None, description="Google AI API key")
    model: str = Field("gemini-pro", description="Gemini model to use")
    temperature: float = Field(0.3, ge=0.0, le=1.0, description="Translation creativity")


class TranslationSettings(BaseModel):
    """Translation-specific settings."""
    
    default_source_lang: LanguageCode = Field(
        LanguageCode.AUTO, 
        description="Default source language"
    )
    default_target_lang: LanguageCode = Field(
        LanguageCode.VIETNAMESE, 
        description="Default target language (Vietnamese priority)"
    )
    preferred_service: TranslationService = Field(
        TranslationService.OPENAI, 
        description="Preferred translation service"
    )
    max_pages: int = Field(50, ge=1, le=100, description="Maximum pages per PDF")
    max_file_size_mb: float = Field(50.0, ge=1.0, le=200.0, description="Maximum file size in MB")
    cache_translations: bool = Field(True, description="Enable translation caching")
    preserve_formatting: bool = Field(True, description="Preserve PDF formatting")
    min_text_length: int = Field(5, ge=0, description="Minimum text length to translate")


class GUISettings(BaseModel):
    """GUI-specific settings."""
    
    window_width: int = Field(1200, ge=800, description="Default window width")
    window_height: int = Field(800, ge=600, description="Default window height")
    theme: Literal["light", "dark", "system"] = Field("system", description="UI theme")
    show_advanced_options: bool = Field(False, description="Show advanced translation options")
    auto_preview: bool = Field(True, description="Auto-preview translations")
    vietnamese_font_priority: bool = Field(True, description="Prioritize Vietnamese fonts")


class ProcessingSettings(BaseModel):
    """PDF processing settings."""
    
    max_workers: int = Field(4, ge=1, le=8, description="Maximum parallel workers")
    timeout_seconds: int = Field(300, ge=30, le=3600, description="Processing timeout")
    quality_check: bool = Field(True, description="Enable translation quality checks")
    backup_originals: bool = Field(True, description="Keep backup of original files")


class AppSettings(BaseModel):
    """Main application settings model."""
    
    # Service configurations
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)
    
    # Application settings
    translation: TranslationSettings = Field(default_factory=TranslationSettings)
    gui: GUISettings = Field(default_factory=GUISettings)
    processing: ProcessingSettings = Field(default_factory=ProcessingSettings)
    
    # Application metadata
    version: str = Field("1.0.0", description="Application version")
    debug_mode: bool = Field(False, description="Enable debug logging")
    
    @validator('translation')
    def validate_translation_settings(cls, v):
        """Validate translation settings for Vietnamese priority."""
        if v.default_target_lang == LanguageCode.AUTO:
            v.default_target_lang = LanguageCode.VIETNAMESE
        return v
    
    def get_active_service_config(self) -> dict:
        """Get configuration for the active translation service."""
        if self.translation.preferred_service == TranslationService.OPENAI:
            return {
                "service": "openai",
                "config": self.openai.dict()
            }
        elif self.translation.preferred_service == TranslationService.GEMINI:
            return {
                "service": "gemini", 
                "config": self.gemini.dict()
            }
        else:
            raise ValueError(f"Unsupported service: {self.translation.preferred_service}")
    
    def validate_service_credentials(self) -> tuple[bool, str]:
        """Validate that required service credentials are available."""
        active_service = self.get_active_service_config()
        service_name = active_service["service"]
        config = active_service["config"]
        
        if not config.get("api_key"):
            return False, f"Missing API key for {service_name}"
        
        return True, "Credentials validated"


class FileMetadata(BaseModel):
    """Metadata for processed PDF files."""
    
    original_path: Path
    filename: str
    file_size_mb: float
    page_count: int
    source_language: Optional[LanguageCode] = None
    target_language: Optional[LanguageCode] = None
    service_used: Optional[TranslationService] = None
    processing_time_seconds: Optional[float] = None
    translation_quality_score: Optional[float] = None
    
    @validator('file_size_mb')
    def validate_file_size(cls, v, values):
        """Validate file size against limits."""
        # This will be checked against AppSettings.translation.max_file_size_mb
        # in the actual processing logic
        return v
    
    @validator('page_count') 
    def validate_page_count(cls, v, values):
        """Validate page count against limits."""
        # This will be checked against AppSettings.translation.max_pages
        # in the actual processing logic
        return v