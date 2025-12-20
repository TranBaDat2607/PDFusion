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
    model: str = Field("gpt-4.1", description="OpenAI model to use")
    base_url: Optional[str] = Field(None, description="Custom API base URL")
    temperature: float = Field(0.3, ge=0.0, le=2.0, description="Translation creativity")
    max_tokens: Optional[int] = Field(None, description="Maximum tokens per request")

    @validator("model")
    def validate_model(cls, v: str) -> str:
        allowed_models = {"gpt-4.1"}
        if v not in allowed_models:
            raise ValueError(f"Unsupported OpenAI model: {v}")
        return v


class GeminiSettings(BaseModel):
    """Google Gemini translation service settings."""
    
    api_key: Optional[str] = Field(None, description="Google AI API key")
    model: str = Field("gemini-1.5-flash", description="Gemini model to use")
    temperature: float = Field(0.3, ge=0.0, le=1.0, description="Translation creativity")

    @validator("model")
    def validate_model(cls, v: str) -> str:
        allowed_models = {"gemini-1.5-flash"}
        if v not in allowed_models:
            raise ValueError(f"Unsupported Gemini model: {v}")
        return v


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

class RAGSettings(BaseModel):
    """RAG (Retrieval-Augmented Generation) settings."""

    enabled: bool = Field(False, description="Enable RAG functionality")
    auto_process_documents: bool = Field(True, description="Auto-process documents for RAG")
    web_research_enabled: bool = Field(True, description="Enable web research integration")


class DeepSearchSettings(BaseModel):
    """Deep search configuration for academic paper research."""

    enabled: bool = Field(True, description="Enable deep search feature")

    # Search parameters
    max_hops: int = Field(3, ge=1, le=5, description="Maximum citation hops")
    max_papers_per_hop: int = Field(5, ge=1, le=10, description="Papers analyzed per hop")
    max_total_papers: int = Field(20, ge=5, le=50, description="Maximum total papers")

    # Search strategy
    follow_citations: bool = Field(True, description="Follow backward citations")
    follow_cited_by: bool = Field(True, description="Follow forward citations (cited-by)")
    recent_papers_only: bool = Field(False, description="Limit to recent papers (3 years)")
    recent_years_threshold: int = Field(3, ge=1, le=10, description="Years for recency filter")

    # API keys (optional)
    pubmed_api_key: Optional[str] = Field(None, description="PubMed NCBI API key (optional)")
    core_api_key: Optional[str] = Field(None, description="CORE API key (required for CORE)")

    # Paper selection
    diversity_weight: float = Field(0.3, ge=0.0, le=1.0, description="Weight for source diversity")
    relevance_weight: float = Field(0.7, ge=0.0, le=1.0, description="Weight for relevance")

    # Caching
    cache_papers: bool = Field(True, description="Cache fetched paper metadata")
    cache_ttl_days: int = Field(7, ge=1, le=30, description="Cache TTL in days")
    cache_dir: str = Field("data/paper_cache", description="Cache directory")

    # Performance
    concurrent_api_calls: int = Field(3, ge=1, le=10, description="Concurrent API requests")
    request_timeout_seconds: int = Field(30, ge=10, le=120, description="API timeout")

    # LLM settings for synthesis
    synthesis_model: str = Field("auto", description="Model for synthesis (auto uses RAG model)")
    synthesis_max_tokens: int = Field(1500, ge=500, le=4000, description="Max tokens for synthesis")


class AppSettings(BaseModel):
    """Main application settings model."""
    
    # Service configurations
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)
    
    # Application settings
    translation: TranslationSettings = Field(default_factory=TranslationSettings)
    gui: GUISettings = Field(default_factory=GUISettings)
    processing: ProcessingSettings = Field(default_factory=ProcessingSettings)
    rag: RAGSettings = Field(default_factory=RAGSettings)
    deep_search: DeepSearchSettings = Field(default_factory=DeepSearchSettings)
    
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