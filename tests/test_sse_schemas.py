"""Drift detector between `processors/events.py`'s dataclasses (and the ad-hoc
dicts in `api/routes/rag.py`) and their Pydantic mirrors in `api/sse_schemas.py`.

Those mirrors are documentation-only (see that module's docstring) — nothing
validates a real payload against them at runtime. This is the test that keeps
them honest: it constructs a representative real event, computes the same
dict a route would actually emit, and checks the mirror both accepts it *and*
declares exactly the same field set. A field renamed on one side and forgotten
on the other fails here, loudly, instead of only showing up as a silently
wrong generated TypeScript type.

Deliberately does not import `desktop_pdf_translator.rag` — that pulls in
torch/chromadb/sentence-transformers, the heavy stack this suite stays clear
of (see `test_sidecar_boot.py`). The two `AskResultPayload` shapes below are
hand-transcribed from `rag/rag_chain.py:answer_question`'s success and
exception paths instead of exercised live.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Type

import pytest
from pydantic import BaseModel

from desktop_pdf_translator.api.sse_schemas import (
    AskResultPayload,
    ChunkReadyEventPayload,
    CompletionEventPayload,
    ErrorEventPayload,
    ParagraphTranslatedEventPayload,
    ProgressEventPayload,
)
from desktop_pdf_translator.processors.events import (
    ChunkReadyEvent,
    CompletionEvent,
    ErrorEvent,
    EventType,
    ParagraphTranslatedEvent,
    ProgressEvent,
)

_BASE_FIELDS = {"type", "timestamp", "session_id"}


def _payload_of(event) -> Dict[str, Any]:
    """The type-specific fields of `event.to_dict()` — what actually rides in
    the SSE `data` body once `api/jobs.py` wraps it, minus the base
    `type`/`timestamp`/`session_id` fields no model here declares (see
    `sse_schemas.py`'s module docstring for why)."""
    return {k: v for k, v in event.to_dict().items() if k not in _BASE_FIELDS}


def _assert_matches(event, model: Type[BaseModel]) -> None:
    payload = _payload_of(event)
    model.model_validate(payload)  # raises on a type mismatch, e.g. a bad coercion
    assert set(payload.keys()) == set(model.model_fields.keys()), (
        f"{type(event).__name__}.to_dict() and {model.__name__} disagree on "
        f"field set: dataclass has {set(payload.keys())}, model has "
        f"{set(model.model_fields.keys())}"
    )


def test_progress_event_matches_payload_model() -> None:
    event = ProgressEvent(
        type=EventType.PROGRESS_UPDATE,
        timestamp=0.0,
        session_id="s1",
        data={},
        stage="Parsing",
        current_step=1,
        total_steps=3,
        progress_percent=33.3,
        message="hello",
    )
    _assert_matches(event, ProgressEventPayload)


def test_error_event_matches_payload_model() -> None:
    """The mid-stream ErrorEvent — re-emitted under the `progress` SSE event
    name, not `error` (see `sse_schemas.ErrorEventPayload`'s docstring)."""
    event = ErrorEvent(
        type=EventType.ERROR,
        timestamp=0.0,
        session_id="s1",
        data={},
        error_type="TranslationProcessError",
        error_message="boom",
        error_details="stack trace",
        recoverable=False,
    )
    _assert_matches(event, ErrorEventPayload)


def test_chunk_ready_event_matches_payload_model() -> None:
    event = ChunkReadyEvent(
        type=EventType.CHUNK_READY,
        timestamp=0.0,
        session_id="s1",
        data={},
        chunk_index=2,
        total_chunks=5,
        pages_in_chunk=(3, 4),
        rolling_pdf_path=Path("paper_translated_v003.pdf"),
        progress_percent=40.0,
        elapsed_seconds=12.5,
        eta_seconds=18.0,
        pages_per_second=0.4,
        total_pages=10,
        pages_to_translate=4,
        cache_hit=False,
        cached_at=None,
    )
    _assert_matches(event, ChunkReadyEventPayload)


def test_paragraph_translated_event_matches_payload_model() -> None:
    event = ParagraphTranslatedEvent(
        type=EventType.PARAGRAPH_TRANSLATED,
        timestamp=0.0,
        session_id="s1",
        data={},
        source_preview="Hello world",
        target_preview="Xin chào thế giới",
        paragraphs_seen=7,
        service="openai",
    )
    _assert_matches(event, ParagraphTranslatedEventPayload)


def test_completion_event_matches_payload_model() -> None:
    event = CompletionEvent(
        type=EventType.FINISH,
        timestamp=0.0,
        session_id="s1",
        data={},
        success=True,
        original_file=Path("paper.pdf"),
        translated_file=Path("paper_translated_v003.pdf"),
        processing_time_seconds=42.0,
        pages_processed=10,
        cache_hit=False,
        cached_at=None,
        target_lang="vi",
        failed_paragraphs=0,
        total_paragraphs=120,
        retry_count=3,
    )
    _assert_matches(event, CompletionEventPayload)


def test_completion_event_all_defaults_matches_payload_model() -> None:
    """`_run_translation` falls back to `job.finish("done", {})` if
    `EventType.FINISH` was never observed — the all-defaults edge case
    `CompletionEventPayload`'s Optional fields exist to cover."""
    event = CompletionEvent(
        type=EventType.FINISH, timestamp=0.0, session_id="s1", data={}, success=None
    )
    payload = _payload_of(event)
    CompletionEventPayload.model_validate(payload)


@pytest.mark.parametrize(
    "example",
    [
        # answer_question's success path (rag_chain.py:111-120).
        {
            "answer": "Because of X.",
            "pdf_references": [
                {
                    "type": "pdf",
                    "page": 3,
                    "text": "some excerpt",
                    "confidence": 0.87,
                    "document_id": "doc1",
                    "document_path": "C:/docs/doc1.pdf",
                    "chunk_id": "doc1-chunk-2",
                    "has_equations": False,
                    "has_tables": True,
                    "has_figures": False,
                }
            ],
            "quality_metrics": {"total_sources": 3.0},
            "processing_time": 1.23,
            "sources_used": {"pdf_sources": 3},
            "timestamp": "2026-09-09T00:00:00",
        },
        # answer_question's own exception handler (rag_chain.py:125-132) — a
        # completely different quality_metrics key set, and no
        # processing_time/sources_used/timestamp.
        {
            "answer": "Sorry, I cannot answer this question due to an error: boom",
            "pdf_references": [],
            "quality_metrics": {"confidence": 0.0, "completeness": 0.0},
            "error": "boom",
        },
    ],
)
def test_ask_result_payload_covers_both_real_shapes(example: Dict[str, Any]) -> None:
    AskResultPayload.model_validate(example)
