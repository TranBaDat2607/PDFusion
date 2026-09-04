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
    CHUNK_READY = "chunk_ready"
    PARAGRAPH_TRANSLATED = "paragraph_translated"


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
class ChunkReadyEvent(ProcessingEvent):
    """A chunk of the source PDF has finished translating.

    `rolling_pdf_path` is always a *full-length* document:
    `_rebuild_sparse_rolling_pdf` fills the finished slots with translated
    chunks and the rest with the original pages, so the viewer keeps its
    scroll position. Chunks are scheduled nearest the reader's page first, so
    `chunk_index` is not a completion count and `pages_in_chunk` names only
    the pages *this* chunk covers — never a range starting at page 1 (#15).
    """

    chunk_index: int
    total_chunks: int
    pages_in_chunk: tuple
    rolling_pdf_path: Path
    progress_percent: float
    # Timing fields populated by the processor when chunk completes. ETA is
    # `None` until we have at least 2 chunks to derive a stable rate.
    elapsed_seconds: Optional[float] = None
    eta_seconds: Optional[float] = None
    pages_per_second: Optional[float] = None
    # Pages in the whole document. `total_chunks` is NOT a page count — Argos
    # runs 3-page chunks (`_PAGES_PER_CHUNK_ARGOS`) — so the UI needs this to
    # say "N of M pages" without guessing.
    total_pages: Optional[int] = None
    # Set when this chunk was served from the PDF-level cache (synthetic event
    # emitted from process_pdf's cache-hit short-circuit). `cached_at` is the
    # ISO8601 timestamp the cache entry was originally written.
    cache_hit: bool = False
    cached_at: Optional[str] = None

    def __post_init__(self):
        self.data = {
            "chunk_index": self.chunk_index,
            "total_chunks": self.total_chunks,
            "pages_in_chunk": list(self.pages_in_chunk),
            "rolling_pdf_path": str(self.rolling_pdf_path),
            "progress_percent": self.progress_percent,
            "elapsed_seconds": self.elapsed_seconds,
            "eta_seconds": self.eta_seconds,
            "pages_per_second": self.pages_per_second,
            "total_pages": self.total_pages,
            "cache_hit": self.cache_hit,
            "cached_at": self.cached_at,
        }


@dataclass
class ParagraphTranslatedEvent(ProcessingEvent):
    """A single paragraph just finished translating. Frontend uses this to
    show a 'live ticker' of EN → VI preview text in the progress overlay.

    `source_preview` / `target_preview` are pre-truncated to ~80 chars to
    keep the SSE payload small and the UI readable."""

    source_preview: str
    target_preview: str
    paragraphs_seen: int
    service: str

    def __post_init__(self):
        self.data = {
            "source_preview": self.source_preview,
            "target_preview": self.target_preview,
            "paragraphs_seen": self.paragraphs_seen,
            "service": self.service,
        }


@dataclass
class CompletionEvent(ProcessingEvent):
    """Processing completion event."""
    
    success: bool
    original_file: Optional[Path] = None
    translated_file: Optional[Path] = None
    processing_time_seconds: Optional[float] = None
    pages_processed: Optional[int] = None
    cache_hit: bool = False
    cached_at: Optional[str] = None
    # Language actually produced, after `process_pdf` resolves the request
    # against config defaults. The frontend names the saved file from this
    # rather than from its own config copy, which the user can change after
    # the run finishes.
    target_lang: Optional[str] = None

    def __post_init__(self):
        """Initialize event data from attributes."""
        self.data = {
            "success": self.success,
            "original_file": str(self.original_file) if self.original_file else None,
            "translated_file": str(self.translated_file) if self.translated_file else None,
            "processing_time_seconds": self.processing_time_seconds,
            "pages_processed": self.pages_processed,
            "cache_hit": self.cache_hit,
            "cached_at": self.cached_at,
            "target_lang": self.target_lang,
        }