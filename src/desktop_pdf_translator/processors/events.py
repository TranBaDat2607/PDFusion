"""
Processing events for PDF translation pipeline.
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any
from enum import Enum
from pathlib import Path


class EventType(str, Enum):
    """Types of processing events."""
    
    PROGRESS_START = "progress_start"
    PROGRESS_UPDATE = "progress_update" 
    PROGRESS_END = "progress_end"
    ERROR = "error"
    FINISH = "finish"
    VALIDATION_START = "validation_start"
    VALIDATION_END = "validation_end"
    TRANSLATION_START = "translation_start"
    TRANSLATION_END = "translation_end"


@dataclass
class ProcessingEvent:
    """Base class for all processing events."""
    
    type: EventType
    timestamp: float
    session_id: str
    data: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary for serialization."""
        return {
            "type": self.type,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            **self.data
        }


@dataclass 
class ProgressEvent(ProcessingEvent):
    """Progress update event."""
    
    stage: str
    current_step: int
    total_steps: int
    progress_percent: float
    message: Optional[str] = None
    
    def __post_init__(self):
        """Initialize event data from attributes."""
        self.data = {
            "stage": self.stage,
            "current_step": self.current_step,
            "total_steps": self.total_steps, 
            "progress_percent": self.progress_percent,
            "message": self.message
        }


@dataclass
class ErrorEvent(ProcessingEvent):
    """Error event."""
    
    error_type: str
    error_message: str
    error_details: Optional[str] = None
    recoverable: bool = False
    
    def __post_init__(self):
        """Initialize event data from attributes.""" 
        self.data = {
            "error_type": self.error_type,
            "error_message": self.error_message,
            "error_details": self.error_details,
            "recoverable": self.recoverable
        }


@dataclass
class CompletionEvent(ProcessingEvent):
    """Processing completion event."""
    
    success: bool
    original_file: Optional[Path] = None
    translated_file: Optional[Path] = None
    processing_time_seconds: Optional[float] = None
    pages_processed: Optional[int] = None
    
    def __post_init__(self):
        """Initialize event data from attributes."""
        self.data = {
            "success": self.success,
            "original_file": str(self.original_file) if self.original_file else None,
            "translated_file": str(self.translated_file) if self.translated_file else None,
            "processing_time_seconds": self.processing_time_seconds,
            "pages_processed": self.pages_processed
        }