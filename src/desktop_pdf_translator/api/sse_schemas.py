"""Pydantic mirrors of the SSE event payloads emitted over `/translate/*`,
`/rag/index/*` and `/rag/ask/*`'s event streams.

None of these are used as `response_model=` on a real route — sse-starlette's
`EventSourceResponse` never validates against a Pydantic model, and adding
these does not change what any route actually sends. They exist purely so
`api/export_openapi.py` can splice accurate shapes into the generated
`text/event-stream` schema — the only way a hand-rolled wire format gets a
real TypeScript type via `openapi-typescript`.

Fields are `Optional[...] = None` wherever the real payload can omit them —
several of these shapes (the RAG index `progress` dict, `EnhancedRAGChain
.answer_question`'s success vs. exception paths) are genuinely inconsistent
today. The goal is an honest description of what ships, not a change to what
ships. See `tests/test_sse_schemas.py` for the drift check against
`processors/events.py`.

The base `type`/`timestamp`/`session_id` fields that `ProcessingEvent.to_dict()`
merges into every `processors/events.py`-derived payload are deliberately not
modeled here: every SSE consumer already gets the event's identity from the
outer `event:` line (see `api/jobs.py:serialize_sse_event`), never from the
payload body, so they carry no information a client needs.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# processors/events.py mirrors — /translate/{job_id}/events
# ---------------------------------------------------------------------------


class ProgressEventPayload(BaseModel):
    """`ProgressEvent.to_dict()` — most `progress` SSE events on
    `/translate/{job_id}/events`."""

    stage: str
    current_step: int
    total_steps: int
    progress_percent: float
    message: Optional[str] = None


class ErrorEventPayload(BaseModel):
    """`ErrorEvent.to_dict()` — a mid-stream processor error, which
    `_run_translation` re-emits as a `progress` SSE event, not an `error` one
    (see `api/routes/translation.py`). Distinct from `JobErrorPayload` below,
    the job-level *terminal* `error` event's shape."""

    error_type: str
    error_message: str
    error_details: Optional[str] = None
    recoverable: bool = False


class ChunkReadyEventPayload(BaseModel):
    """`ChunkReadyEvent.to_dict()` — the `chunk_ready` SSE event."""

    chunk_index: int
    total_chunks: int
    # Always a [first, last] page range, 1-indexed and inclusive — see
    # `lib/translation-progress.ts`'s `ChunkReadyLike`.
    pages_in_chunk: Tuple[int, int]
    rolling_pdf_path: str
    progress_percent: float
    elapsed_seconds: Optional[float] = None
    eta_seconds: Optional[float] = None
    pages_per_second: Optional[float] = None
    total_pages: Optional[int] = None
    cache_hit: bool = False
    cached_at: Optional[str] = None


class ParagraphTranslatedEventPayload(BaseModel):
    """`ParagraphTranslatedEvent.to_dict()` — the `paragraph_translated` event."""

    source_preview: str
    target_preview: str
    paragraphs_seen: int
    service: str


class CompletionEventPayload(BaseModel):
    """`CompletionEvent.to_dict()` — the terminal `done` payload for
    `/translate/{job_id}/events`. Every field is Optional (mirroring the
    dataclass's own defaults) because `_run_translation` falls back to
    `job.finish("done", {})` if `EventType.FINISH` was never observed."""

    success: Optional[bool] = None
    original_file: Optional[str] = None
    translated_file: Optional[str] = None
    processing_time_seconds: Optional[float] = None
    pages_processed: Optional[int] = None
    cache_hit: bool = False
    cached_at: Optional[str] = None
    target_lang: Optional[str] = None
    failed_paragraphs: int = 0
    total_paragraphs: int = 0
    retry_count: int = 0


# ---------------------------------------------------------------------------
# Job-level shapes with no ProcessingEvent dataclass behind them
# ---------------------------------------------------------------------------


class JobErrorPayload(BaseModel):
    """The terminal `error` event on all three SSE streams — an uncaught
    exception in the job worker, as `{"message": str(exc)}`.

    `code` is set only where the client acts on the failure rather than just
    showing it: `index_unavailable` from `/rag/ask`, when the document's chat
    index had to be cleared and the panel should index it again."""

    message: str
    code: Optional[str] = None


class CancelPayload(BaseModel):
    """The terminal `cancelled` event for `/translate/{job_id}/events`
    (`_build_cancel_payload`, `api/routes/translation.py`)."""

    translated_file: Optional[str] = None
    original_file: str


class EmptyPayload(BaseModel):
    """The terminal `cancelled` event on both RAG streams — `api/routes/rag.py`
    sends a literal `{}` for it today."""


# ---------------------------------------------------------------------------
# /rag/index/{job_id}/events
# ---------------------------------------------------------------------------


class IndexProgressPayload(BaseModel):
    """`progress` events from `_run_index`. `chunks` is only sent from two of
    the three call sites."""

    stage: str
    progress: int
    chunks: Optional[int] = None


class IndexDonePayload(BaseModel):
    document_id: str
    chunks: int
    cached: bool


# ---------------------------------------------------------------------------
# /rag/ask/{job_id}/events
# ---------------------------------------------------------------------------


class AskProgressPayload(BaseModel):
    message: str
    progress: int


class PdfReferencePayload(BaseModel):
    """One entry of `AskResultPayload.pdf_references`
    (`rag_chain._create_pdf_references`). `page` is 1-indexed, or `None` when
    the chunk has no usable page — see `rag_chain._display_page`."""

    type: str = "pdf"
    page: Optional[int] = None
    text: str
    confidence: float
    document_id: str
    document_path: str
    chunk_id: str
    has_equations: bool = False
    has_tables: bool = False
    has_figures: bool = False
    # Present only when the source chunk's metadata carries an `elements` list
    # whose first element has a `bbox` — see `_create_pdf_references`.
    bbox: Optional[List[float]] = None


class AskResultPayload(BaseModel):
    """The `answer` and `done` payload for `/rag/ask/{job_id}/events` — the
    dict `EnhancedRAGChain.answer_question` returns, verbatim.

    Only ever a real answer. A failure ends the stream with `error` instead;
    it used to arrive here, as an answer whose text was the error (#31).
    `elapsed_seconds` is added by `_run_ask` only for the `done` event, never
    for `answer`.
    """

    answer: str
    pdf_references: List[PdfReferencePayload] = Field(default_factory=list)
    quality_metrics: Dict[str, float] = Field(default_factory=dict)
    processing_time: Optional[float] = None
    sources_used: Optional[Dict[str, int]] = None
    timestamp: Optional[str] = None
    elapsed_seconds: Optional[float] = None
