"""
Main PDF processing pipeline with BabelDOC integration and Vietnamese optimization.
"""

import asyncio
import logging
import os
import time
import uuid
from pathlib import Path
from typing import AsyncGenerator, Optional, Dict, Any

import fitz  # PyMuPDF

from babeldoc.format.pdf.high_level import async_translate as babeldoc_translate
from babeldoc.format.pdf.translation_config import TranslationConfig as BabelDOCConfig
from babeldoc.format.pdf.translation_config import WatermarkOutputMode as BabelDOCWatermarkMode

from ..config import get_settings, FileMetadata, LanguageCode, TranslationService
from ..translators import TranslatorFactory
from ..translators.argos_translator import ArgosTranslator
from .events import (
    ProcessingEvent,
    ProgressEvent,
    ErrorEvent,
    CompletionEvent,
    ChunkReadyEvent,
    ParagraphTranslatedEvent,
    EventType,
)
from .exceptions import ProcessingError, BabelDOCError, FileValidationError, ConfigurationError


# Number of source pages per BabelDOC sub-job. 1 = page-by-page streaming: the
# viewer hot-swaps every time a single page finishes its full BabelDOC pipeline
# (translate → typeset → render → save). Combined with `_MAX_PARALLEL_CHUNKS`
# below, this gives the user the maximum streaming feel.
_PAGES_PER_CHUNK = 1

# Argos-only: pages per chunk when translating offline. BabelDOC reloads the
# DocLayoutYOLO ONNX model on every chunk (~3-4s each), so 1-page chunks pay
# that cost N times for an N-page PDF. With Argos, translation throughput is
# fast enough that the layout-model reload dominates per-chunk overhead — so
# we trade a little streaming granularity for big wins on overhead. The user
# still sees pages appear in groups of 3 with the rolling viewer.
_PAGES_PER_CHUNK_ARGOS = 3

# Argos-only cap on parallel chunks. Each in-flight BabelDOC sub-job peaks at
# ~9 GB RAM on an academic paper (DocLayoutYOLO + IR + font maps). Four
# parallel chunks = ~36 GB → swap thrashing on most desktops, which is what
# produced the 120-second "PDF save timeout" warnings. Argos itself is the
# bottleneck-side rate limiter and gains nothing from extra parallelism, so
# we cap conservatively.
_MAX_PARALLEL_CHUNKS_ARGOS = 2

# How many BabelDOC sub-jobs may be in flight at once. BabelDOC's pipeline is
# monolithic (you can't externally separate translate from render), but by
# running multiple jobs concurrently we get natural pipelining: while job N is
# in its typeset/render/save phase, jobs N+1..N+k can be in their translate
# phase calling Argos. The shared `ArgosTranslator` instance has a single
# batch queue, so the 4 BabelDOC worker threads from each job naturally feed
# one Argos batch at a time — no double-buffering needed, no contention beyond
# Argos's own CTranslate2 serialization.
#
# Higher values increase memory (each BabelDOC job holds its own IR/font maps)
# but improve pipeline depth. 4 is a reasonable default for desktop machines.
_MAX_PARALLEL_CHUNKS = 4


def _effective_parallel_chunks(settings, translator=None) -> int:
    """Resolve the chunk-concurrency knob.

    `processing.max_parallel_chunks = 0` (default) means auto: roughly half the
    CPU cores, clamped to [2, 8]. Each in-flight BabelDOC sub-job carries
    ~150-300 MB of intermediate state on a typical academic paper, so we leave
    half the cores (and roughly half the RAM headroom) to the rest of the app
    plus the WebView2 renderer.

    When `translator` is an ArgosTranslator, the result is clamped down to
    `_MAX_PARALLEL_CHUNKS_ARGOS` to control memory — Argos throughput
    saturates at low parallelism anyway, and per-chunk RAM is ~9 GB on
    formula-heavy papers.
    """
    try:
        configured = int(settings.processing.max_parallel_chunks)
    except (AttributeError, TypeError, ValueError):
        configured = 0
    if configured > 0:
        base = configured
    else:
        cpu = os.cpu_count() or 4
        base = max(2, min(8, cpu // 2))
    if isinstance(translator, ArgosTranslator):
        return min(base, _MAX_PARALLEL_CHUNKS_ARGOS)
    return base


def _effective_pages_per_chunk(translator) -> int:
    """Chunk size in pages. Argos uses larger chunks to amortize BabelDOC's
    per-chunk DocLayoutYOLO ONNX reload cost over more pages."""
    if isinstance(translator, ArgosTranslator):
        return _PAGES_PER_CHUNK_ARGOS
    return _PAGES_PER_CHUNK


logger = logging.getLogger(__name__)


class PDFProcessor:
    """
    Main PDF processing pipeline with BabelDOC integration.
    
    Handles file validation, translation processing, and progress tracking
    with special optimizations for Vietnamese language.
    """
    
    def __init__(self):
        """Initialize PDF processor."""
        self.settings = get_settings()
        self.session_id = None
        self._current_task = None
        # Captured at process_pdf() entry so the API layer can locate the
        # latest rolling translated PDF when the user cancels mid-run.
        self._output_dir: Optional[Path] = None
        self._input_stem: Optional[str] = None
        # Priority anchor for chunk scheduling — the 0-indexed page the viewer
        # is currently showing. Workers prefer chunks whose page index is
        # closest to this anchor. Updated live via `reprioritize()` as the
        # user scrolls. None until process_pdf() initializes it.
        self._priority_anchor: Optional[int] = None
        self._priority_lock: Optional[asyncio.Lock] = None
        # Paragraph-ticker plumbing. The translator callback fires from
        # BabelDOC's worker threads; we bridge back to the asyncio loop with
        # call_soon_threadsafe. The queue is intentionally bounded so a runaway
        # translator can't blow memory; on overflow we drop oldest (the ticker
        # only needs the *latest* paragraph anyway).
        self._paragraph_queue: Optional[asyncio.Queue] = None
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self._paragraphs_seen: int = 0
        self._service_name: str = "argos"
    
    async def process_pdf(
        self,
        file_path: Path,
        source_lang: Optional[LanguageCode] = None,
        target_lang: Optional[LanguageCode] = None,
        translation_service: Optional[TranslationService] = None,
        output_dir: Optional[Path] = None,
        visible_page: int = 1,
    ) -> AsyncGenerator[ProcessingEvent, None]:
        """
        Process PDF file with translation.
        
        Args:
            file_path: Path to input PDF file
            source_lang: Source language (optional, uses config default)
            target_lang: Target language (optional, uses config default) 
            translation_service: Translation service (optional, uses config default)
            output_dir: Output directory (optional, uses temp directory)
            
        Yields:
            ProcessingEvent: Progress updates and completion events
        """
        self.session_id = str(uuid.uuid4())
        start_time = time.time()
        
        try:
            # Use provided languages or fallback to config
            source_lang = source_lang or self.settings.translation.default_source_lang
            target_lang = target_lang or self.settings.translation.default_target_lang
            translation_service = translation_service or self.settings.translation.preferred_service

            # Soft-fallback: if the user picked an LLM service but didn't
            # provide a key, silently fall back to Argos and let the UI toast.
            requested_service = translation_service
            translation_service = self._resolve_effective_service(translation_service)
            fell_back = translation_service != requested_service

            # Log the actual languages being used
            logger.info(f"Using languages - Source: {source_lang}, Target: {target_lang}")
            
            # Set output directory
            if output_dir is None:
                output_dir = Path.cwd() / "translated_pdfs"
            output_dir.mkdir(parents=True, exist_ok=True)
            self._output_dir = output_dir
            self._input_stem = file_path.stem

            logger.info(f"Starting PDF processing session {self.session_id}")
            logger.info(f"File: {file_path}, {source_lang} -> {target_lang}, Service: {translation_service}")
            
            # Step 1: File validation
            yield ProgressEvent(
                type=EventType.PROGRESS_START,
                timestamp=time.time(),
                session_id=self.session_id,
                data={},
                stage="Validating file",
                current_step=1,
                total_steps=4,
                progress_percent=0.0,
                message=f"Validating {file_path.name}"
            )
            
            file_metadata = await self._validate_file(file_path)
            
            yield ProgressEvent(
                type=EventType.PROGRESS_UPDATE,
                timestamp=time.time(),
                session_id=self.session_id,
                data={},
                stage="File validation complete",
                current_step=1,
                total_steps=4,
                progress_percent=25.0,
                message=f"File validated: {file_metadata.page_count} pages, {file_metadata.file_size_mb:.1f} MB"
            )
            
            # Step 2: Create translator
            if fell_back:
                yield ProgressEvent(
                    type=EventType.PROGRESS_UPDATE,
                    timestamp=time.time(),
                    session_id=self.session_id,
                    data={},
                    stage="fallback",
                    current_step=2,
                    total_steps=4,
                    progress_percent=28.0,
                    message=(
                        f"No {requested_service.value} API key configured — "
                        f"falling back to Argos (offline) for this run."
                    ),
                )

            yield ProgressEvent(
                type=EventType.PROGRESS_UPDATE,
                timestamp=time.time(),
                session_id=self.session_id,
                data={},
                stage="Initializing translator",
                current_step=2,
                total_steps=4,
                progress_percent=30.0,
                message=f"Setting up {translation_service} translator"
            )
            
            # Paragraph-ticker plumbing — capture the loop so the
            # translator's worker-thread callback can hop back here.
            self._paragraph_queue = asyncio.Queue(maxsize=64)
            self._event_loop = asyncio.get_running_loop()
            self._paragraphs_seen = 0
            self._service_name = translation_service.value

            translator = TranslatorFactory.create_translator(
                service=translation_service,
                lang_in=source_lang,
                lang_out=target_lang,
                on_paragraph_translated=self._handle_paragraph,
            )
            
            yield ProgressEvent(
                type=EventType.PROGRESS_UPDATE,
                timestamp=time.time(),
                session_id=self.session_id,
                data={},
                stage="Translator ready",
                current_step=2,
                total_steps=4,
                progress_percent=40.0,
                message=f"Translator initialized: {translator}"
            )
            
            # Seed the priority anchor from the page the viewer is currently
            # showing. Workers will pick chunks whose distance from this is
            # smallest — so the user's visible page translates first.
            self._priority_anchor = max(0, visible_page - 1)
            self._priority_lock = asyncio.Lock()

            # Step 3: BabelDOC processing
            logger.info(
                "Using BabelDOC processing (priority anchor = page %d)",
                visible_page,
            )
            async for event in self._process_with_babeldoc(
                file_path, translator, output_dir, file_metadata, start_time
            ):
                yield event
            
            # Step 4: Completion
            processing_time = time.time() - start_time
            
            # Find output files
            translated_file = self._find_translated_file(output_dir, file_path.stem)
            logger.info(
                "CompletionEvent: output_dir=%s, stem=%s, translated_file=%s",
                output_dir,
                file_path.stem,
                translated_file,
            )

            yield CompletionEvent(
                type=EventType.FINISH,
                timestamp=time.time(),
                session_id=self.session_id,
                data={},
                success=True,
                original_file=file_path,
                translated_file=translated_file,
                processing_time_seconds=processing_time,
                pages_processed=file_metadata.page_count
            )
            
        except ProcessingError as e:
            # Handle known processing errors
            yield ErrorEvent(
                type=EventType.ERROR,
                timestamp=time.time(),
                session_id=self.session_id,
                data={},
                error_type=e.__class__.__name__,
                error_message=e.message,
                error_details=e.details,
                recoverable=False
            )
            raise
            
        except Exception as e:
            # Handle unexpected errors
            logger.exception(f"Unexpected error in PDF processing: {e}")
            yield ErrorEvent(
                type=EventType.ERROR,
                timestamp=time.time(),
                session_id=self.session_id,
                data={},
                error_type="UnexpectedError",
                error_message=str(e),
                error_details=None,
                recoverable=False
            )
            raise ProcessingError(f"Processing failed: {e}", details=str(e))
    
    def _resolve_effective_service(
        self, requested: TranslationService
    ) -> TranslationService:
        """Pick the service to actually use given current credentials.

        Argos (offline) is always usable. For LLM services, fall back to Argos
        when the user has no key configured. Matches the product rule:
        "Argos is default; LLM wins when a key exists".
        """
        if self.settings.has_api_key(requested):
            return requested
        logger.info(
            "No API key for %s — falling back to Argos for this run", requested
        )
        return TranslationService.ARGOS

    async def _validate_file(self, file_path: Path) -> FileMetadata:
        """Validate PDF file and extract metadata."""
        try:
            if not file_path.exists():
                raise FileValidationError(f"File does not exist: {file_path}")
            
            if not file_path.suffix.lower() == '.pdf':
                raise FileValidationError(f"File is not a PDF: {file_path}")
            
            # Get file size
            file_size_mb = file_path.stat().st_size / (1024 * 1024)
            
            # Check file size limit
            if file_size_mb > self.settings.translation.max_file_size_mb:
                raise FileValidationError(
                    f"File too large: {file_size_mb:.1f} MB > {self.settings.translation.max_file_size_mb} MB"
                )
            
            # Open PDF and get page count
            try:
                doc = fitz.open(file_path)
                page_count = len(doc)
                doc.close()
            except Exception as e:
                raise FileValidationError(f"Cannot open PDF file: {e}")
            
            # Check page count limit
            if page_count > self.settings.translation.max_pages:
                raise FileValidationError(
                    f"Too many pages: {page_count} > {self.settings.translation.max_pages}"
                )
            
            return FileMetadata(
                original_path=file_path,
                filename=file_path.name,
                file_size_mb=file_size_mb,
                page_count=page_count
            )
            
        except FileValidationError:
            raise
        except Exception as e:
            raise FileValidationError(f"File validation failed: {e}")
    
    async def _process_with_babeldoc(
        self,
        file_path: Path,
        translator,
        output_dir: Path,
        file_metadata: FileMetadata,
        job_start_time: Optional[float] = None,
    ) -> AsyncGenerator[ProcessingEvent, None]:
        """Process PDF using BabelDOC with 1-page chunks running in a parallel
        render pipeline.

        BabelDOC's pipeline is monolithic — every call to `babeldoc_translate()`
        runs all 13 stages (layout → translate → typeset → render → save) and
        there is no external hook to peel "translate" apart from "render". So
        we get streaming + pipelining by structuring it at the orchestration
        layer:

        1. Split the input into 1-page chunks up front.
        2. Launch up to `_MAX_PARALLEL_CHUNKS` BabelDOC sub-jobs concurrently.
           While chunk N is in its typeset/render/save phase, chunk N+1 can be
           in its translate phase calling Argos.
        3. The single shared `ArgosTranslator` instance has one batch queue;
           paragraphs from all concurrent chunks fill the same batches of 4,
           and CTranslate2 serializes the actual NMT inference. Argos is the
           natural rate limiter — extra parallelism doesn't make it faster,
           but it does hide BabelDOC's per-chunk non-translate overhead.
        4. Completed chunks are merged into a rolling versioned PDF (in
           strict page order, even when chunks finish out of order) and a
           `chunk_ready` SSE event is emitted so the viewer hot-swaps.

        The translator instance is reused across chunks so the Argos batch
        queue and process-lifetime translation cache (argos_translator.py)
        persist — repeated paragraphs across chunks translate for free.
        """
        chunk_tasks: list[asyncio.Task] = []
        max_parallel = _effective_parallel_chunks(self.settings, translator)
        pages_per_chunk = _effective_pages_per_chunk(translator)
        try:
            logger.info(
                "Starting BabelDOC processing (parallel chunked, "
                "pages_per_chunk=%d, max_parallel=%d, translator=%s)",
                pages_per_chunk, max_parallel, type(translator).__name__,
            )

            chunk_work_dir = output_dir / f"{file_path.stem}_chunk_work"
            chunks_in_dir = chunk_work_dir / "in"
            chunks_out_root = chunk_work_dir / "out"
            chunks_in_dir.mkdir(parents=True, exist_ok=True)
            chunks_out_root.mkdir(parents=True, exist_ok=True)

            chunks = self._split_input_into_chunks(
                file_path, chunks_in_dir, pages_per_chunk=pages_per_chunk
            )
            total_chunks = len(chunks)
            logger.info(
                "Split %s (%d pages) into %d chunk(s) of up to %d page(s)",
                file_path.name, file_metadata.page_count, total_chunks,
                pages_per_chunk,
            )

            yield ProgressEvent(
                type=EventType.PROGRESS_UPDATE,
                timestamp=time.time(),
                session_id=self.session_id,
                data={},
                stage="Starting parallel BabelDOC pipeline",
                current_step=3,
                total_steps=4,
                progress_percent=50.0,
                message=(
                    f"Pipelining {total_chunks} page(s), "
                    f"up to {max_parallel} in flight"
                ),
            )

            # Priority-driven scheduling. Up to `max_parallel` workers run
            # concurrently, each picking the **pending** chunk whose page
            # index is closest to the current `_priority_anchor` (the page
            # the user is looking at). Reprioritization via
            # `reprioritize()` updates the anchor and the next worker pick
            # sees the new value.
            chunk_results: list[Optional[Path]] = [None] * total_chunks
            chunk_errors: list[Optional[BaseException]] = [None] * total_chunks
            pending: set[int] = set(range(total_chunks))
            completion_queue: asyncio.Queue[int] = asyncio.Queue()
            assert self._priority_lock is not None  # set in process_pdf()
            priority_lock = self._priority_lock

            async def pick_next() -> Optional[int]:
                """Pop the pending chunk closest to the priority anchor.
                Returns None when the pool is empty."""
                async with priority_lock:
                    if not pending:
                        return None
                    anchor = self._priority_anchor or 0
                    # Smallest absolute distance wins; ties break to the
                    # lower index for deterministic progress on flat priors.
                    target = min(pending, key=lambda i: (abs(i - anchor), i))
                    pending.discard(target)
                    return target

            async def run_one_chunk(idx: int) -> None:
                chunk_path, page_range = chunks[idx]
                try:
                    chunk_out_dir = chunks_out_root / f"chunk_{idx:03d}"
                    chunk_out_dir.mkdir(parents=True, exist_ok=True)
                    config = self._create_babeldoc_config(
                        chunk_path, translator, chunk_out_dir
                    )
                    logger.info(
                        "Chunk %d/%d (page %d): BabelDOC pipeline start",
                        idx + 1, total_chunks, page_range[0],
                    )
                    async for event in babeldoc_translate(config):
                        etype = event["type"]
                        if etype == "error":
                            raise BabelDOCError(
                                message=(
                                    f"BabelDOC chunk {idx + 1} failed: "
                                    f"{event.get('error', 'Unknown error')}"
                                ),
                                details=event.get("details"),
                            )
                        elif etype == "finish":
                            logger.info(
                                "Chunk %d/%d (page %d): BabelDOC complete",
                                idx + 1, total_chunks, page_range[0],
                            )
                            break
                    translated = self._find_translated_file(
                        chunk_out_dir, chunk_path.stem
                    )
                    if translated is None:
                        raise BabelDOCError(
                            f"Could not locate translated PDF for chunk "
                            f"{idx + 1} in {chunk_out_dir}"
                        )
                    chunk_results[idx] = translated
                except BaseException as exc:
                    chunk_errors[idx] = exc
                finally:
                    await completion_queue.put(idx)

            async def worker() -> None:
                while True:
                    nxt = await pick_next()
                    if nxt is None:
                        return
                    await run_one_chunk(nxt)

            chunk_tasks = [
                asyncio.create_task(worker(), name=f"babeldoc-worker-{w:02d}")
                for w in range(max_parallel)
            ]

            # Unified event multiplexer: chunk completions AND the
            # paragraph-ticker tick feed into a single asyncio.Queue. The
            # generator drains it and yields events in arrival order until
            # the chunk-completion task signals done via `None`.
            events_queue: asyncio.Queue = asyncio.Queue()
            _SENTINEL = object()
            chunk_weight = 40.0 / total_chunks

            async def chunk_completion_producer() -> None:
                completed_count_local = 0
                try:
                    while completed_count_local < total_chunks:
                        idx_local = await completion_queue.get()
                        completed_count_local += 1
                        await events_queue.put(("chunk", idx_local, completed_count_local))
                finally:
                    await events_queue.put(_SENTINEL)

            async def paragraph_ticker() -> None:
                """5 Hz tick: emit only the most-recently translated paragraph.
                We drain the queue down to its latest entry each tick — the UI
                only ever shows one preview row so dropped intermediates are
                invisible. Exits cleanly on CancelledError."""
                assert self._paragraph_queue is not None
                pq = self._paragraph_queue
                try:
                    while True:
                        await asyncio.sleep(0.2)
                        latest = None
                        while not pq.empty():
                            try:
                                latest = pq.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                        if latest is None:
                            continue
                        s, t = latest
                        await events_queue.put(("paragraph", s, t))
                except asyncio.CancelledError:
                    return

            completion_task = asyncio.create_task(
                chunk_completion_producer(), name="chunk-completion-producer"
            )
            ticker_task = asyncio.create_task(
                paragraph_ticker(), name="paragraph-ticker"
            )
            chunk_tasks.append(completion_task)
            chunk_tasks.append(ticker_task)

            try:
                while True:
                    event = await events_queue.get()
                    if event is _SENTINEL:
                        break
                    kind = event[0]
                    if kind == "paragraph":
                        _, s, t = event
                        yield ParagraphTranslatedEvent(
                            type=EventType.PARAGRAPH_TRANSLATED,
                            timestamp=time.time(),
                            session_id=self.session_id,
                            data={},
                            source_preview=s,
                            target_preview=t,
                            paragraphs_seen=self._paragraphs_seen,
                            service=self._service_name,
                        )
                        continue

                    # kind == "chunk"
                    _, idx, completed_count = event
                    err = chunk_errors[idx]
                    if err is not None:
                        if isinstance(err, BabelDOCError):
                            raise err
                        raise BabelDOCError(
                            f"BabelDOC processing error in chunk {idx + 1}: {err}",
                            original_error=err,
                        )

                    _, page_range = chunks[idx]
                    rolling_path = (
                        output_dir
                        / f"{file_path.stem}_translated_v{completed_count:03d}.pdf"
                    )
                    self._rebuild_sparse_rolling_pdf(
                        rolling_path,
                        original_path=file_path,
                        chunks=chunks,
                        chunk_results=chunk_results,
                    )

                    global_progress = 50.0 + chunk_weight * completed_count

                    # ETA: need ≥2 completed chunks for a stable rate.
                    elapsed: Optional[float] = None
                    pages_per_second: Optional[float] = None
                    eta_seconds: Optional[float] = None
                    if job_start_time is not None:
                        elapsed = time.time() - job_start_time
                        if completed_count >= 2 and elapsed and elapsed > 0:
                            pages_per_second = completed_count / elapsed
                            remaining_chunks = total_chunks - completed_count
                            if pages_per_second > 0 and remaining_chunks > 0:
                                eta_seconds = remaining_chunks / pages_per_second

                    yield ChunkReadyEvent(
                        type=EventType.CHUNK_READY,
                        timestamp=time.time(),
                        session_id=self.session_id,
                        data={},
                        chunk_index=idx,
                        total_chunks=total_chunks,
                        pages_in_chunk=(page_range[0], page_range[1]),
                        rolling_pdf_path=rolling_path,
                        progress_percent=global_progress,
                        elapsed_seconds=elapsed,
                        eta_seconds=eta_seconds,
                        pages_per_second=pages_per_second,
                    )
                    yield ProgressEvent(
                        type=EventType.PROGRESS_UPDATE,
                        timestamp=time.time(),
                        session_id=self.session_id,
                        data={},
                        stage=f"Page {page_range[1]}/{file_metadata.page_count} ready",
                        current_step=3,
                        total_steps=4,
                        progress_percent=global_progress,
                        message=(
                            f"Translated page {page_range[1]} of "
                            f"{file_metadata.page_count}"
                        ),
                    )
            finally:
                # Stop the ticker; completion_task is already done by sentinel.
                ticker_task.cancel()

            yield ProgressEvent(
                type=EventType.PROGRESS_UPDATE,
                timestamp=time.time(),
                session_id=self.session_id,
                data={},
                stage="BabelDOC processing complete",
                current_step=3,
                total_steps=4,
                progress_percent=90.0,
                message="All pages translated",
            )

        except BabelDOCError:
            raise
        except Exception as e:
            logger.exception(f"BabelDOC processing error: {e}")
            raise BabelDOCError(f"BabelDOC processing error: {e}", original_error=e)
        finally:
            # On error, cancel, or normal exit: ensure no worker task is left
            # running. asyncio.gather with return_exceptions consumes any
            # CancelledError so it doesn't propagate out of the cleanup.
            unfinished_tasks = [t for t in chunk_tasks if not t.done()]
            for t in unfinished_tasks:
                t.cancel()
            if unfinished_tasks:
                await asyncio.gather(*unfinished_tasks, return_exceptions=True)

    def _split_input_into_chunks(
        self,
        input_path: Path,
        chunks_dir: Path,
        pages_per_chunk: int = _PAGES_PER_CHUNK,
    ) -> list:
        """Split a PDF into N-page chunks on disk.

        Returns a list of `(chunk_path, (first_page, last_page))` tuples,
        where pages are 1-indexed and inclusive (UI-friendly).
        """
        chunks_dir.mkdir(parents=True, exist_ok=True)
        src = fitz.open(input_path)
        try:
            total = src.page_count
            chunks = []
            for chunk_idx, start in enumerate(range(0, total, pages_per_chunk)):
                end = min(start + pages_per_chunk, total)
                chunk_doc = fitz.open()
                chunk_doc.insert_pdf(src, from_page=start, to_page=end - 1)
                chunk_path = (
                    chunks_dir
                    / f"{input_path.stem}_chunk{chunk_idx:03d}.pdf"
                )
                chunk_doc.save(chunk_path)
                chunk_doc.close()
                chunks.append((chunk_path, (start + 1, end)))
            return chunks
        finally:
            src.close()

    def _handle_paragraph(self, source: str, target: str) -> None:
        """Thread-safe paragraph callback. Called from the translator's
        worker thread; bridges back to the asyncio loop so the ticker
        consumer can pull from the queue without locks. Bounded queue: drop
        oldest on overflow — the UI only ever shows the *latest* paragraph,
        so missing intermediate ones is fine."""
        if self._paragraph_queue is None or self._event_loop is None:
            return
        # Trim previews to keep SSE payload small and the UI readable.
        s = (source or "").replace("\n", " ").strip()[:80]
        t = (target or "").replace("\n", " ").strip()[:80]
        if not s or not t:
            return
        try:
            self._event_loop.call_soon_threadsafe(self._enqueue_paragraph, (s, t))
        except RuntimeError:
            # Loop shutting down; drop silently.
            pass

    def _enqueue_paragraph(self, payload: tuple[str, str]) -> None:
        """Runs on the asyncio loop. Drops oldest on overflow so a fast
        translator never blocks. Increments `_paragraphs_seen` so the UI
        can show a count."""
        if self._paragraph_queue is None:
            return
        self._paragraphs_seen += 1
        try:
            self._paragraph_queue.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                self._paragraph_queue.get_nowait()
                self._paragraph_queue.put_nowait(payload)
            except Exception:
                pass

    async def reprioritize(self, visible_page: int) -> None:
        """Update the priority anchor — the next worker pick will prefer
        chunks near `visible_page`. Best-effort: chunks already in flight
        finish; only pending chunks see the new priority.

        Safe to call from any asyncio context. Called from the
        `/translate/{job_id}/reprioritize` endpoint as the user scrolls."""
        if self._priority_lock is None:
            self._priority_anchor = max(0, visible_page - 1)
            return
        async with self._priority_lock:
            self._priority_anchor = max(0, visible_page - 1)
        logger.info("Priority anchor updated → page %d (0-indexed %d)",
                    visible_page, self._priority_anchor)

    def _rebuild_sparse_rolling_pdf(
        self,
        rolling_path: Path,
        original_path: Path,
        chunks: list,
        chunk_results: list,
    ) -> None:
        """Build a full N-page rolling PDF where translated chunk slots are
        filled from `chunk_results` and pending slots fall back to the
        original PDF's pages. The viewer always sees a complete N-page
        document, so scroll position stays stable as out-of-order chunks
        land. PyMuPDF's `insert_pdf` is object-level (no re-render) — a
        20-page rebuild typically takes ~50-100ms."""
        merged = fitz.open()
        src = fitz.open(original_path)
        try:
            for idx, (_, page_range) in enumerate(chunks):
                translated = chunk_results[idx]
                if translated is not None:
                    with fitz.open(translated) as chunk_doc:
                        merged.insert_pdf(chunk_doc)
                else:
                    # Fall back to original pages (1-indexed inclusive →
                    # 0-indexed inclusive for PyMuPDF).
                    start_0 = page_range[0] - 1
                    end_0 = page_range[1] - 1
                    merged.insert_pdf(src, from_page=start_0, to_page=end_0)
            merged.save(rolling_path)
        finally:
            src.close()
            merged.close()

    def _rebuild_rolling_pdf(
        self, rolling_path: Path, translated_chunk_paths: list
    ) -> None:
        """Concatenate translated chunks-so-far into a single rolling PDF.

        Rebuild from scratch each time. `fitz.insert_pdf` does not re-render
        page content (it copies the underlying objects), so this is fast
        even for many chunks — ~tens of ms per 5 pages.
        """
        merged = fitz.open()
        try:
            for cp in translated_chunk_paths:
                with fitz.open(cp) as chunk_doc:
                    merged.insert_pdf(chunk_doc)
            merged.save(rolling_path)
        finally:
            merged.close()
    
    async def _process_without_babeldoc(
        self,
        file_path: Path,
        translator,
        output_dir: Path,
        file_metadata: FileMetadata
    ) -> AsyncGenerator[ProcessingEvent, None]:
        """Fallback processing without BabelDOC."""
        logger.warning("Using fallback processing - BabelDOC not available or failed")
        
        yield ProgressEvent(
            type=EventType.PROGRESS_UPDATE,
            timestamp=time.time(),
            session_id=self.session_id,
            data={},
            stage="Fallback processing",
            current_step=3,
            total_steps=4,
            progress_percent=50.0,
            message="BabelDOC not available, using fallback method"
        )
        
        # This would implement a basic PDF processing pipeline
        # For now, just simulate processing
        await asyncio.sleep(2)  # Simulate processing time
        
        yield ProgressEvent(
            type=EventType.PROGRESS_UPDATE,
            timestamp=time.time(),
            session_id=self.session_id,
            data={},
            stage="Fallback processing complete",
            current_step=3,
            total_steps=4,
            progress_percent=90.0,
            message="Basic processing completed"
        )
    
    def _create_babeldoc_config(self, file_path: Path, translator, output_dir: Path):
        """Create BabelDOC configuration."""
        # Get settings for additional parameters
        translation_settings = self.settings.translation
        processing_settings = self.settings.processing
        
        # Log the configuration for debugging
        logger.info(f"Creating BabelDOC config with:")
        logger.info(f"  translator: {translator}")
        logger.info(f"  input_file: {file_path}")
        logger.info(f"  lang_in: {translator.lang_in}")
        logger.info(f"  lang_out: {translator.lang_out}")
        logger.info(f"  output_dir: {output_dir}")
        
        # Per-translator overrides for heavy non-translation stages. Argos
        # has no LLM available, so any flag that secretly triggers an LLM
        # call (glossary extraction) is pure overhead — and skipping the
        # SSIM scanned-page detector + the compatibility-enhancement pass
        # both shave significant wall time on academic papers. LLM paths
        # keep the previous behavior so glossary terms and OCR-quality
        # detection still work for paid backends.
        is_argos = isinstance(translator, ArgosTranslator)

        # Configure for single translated PDF output only (no dual, no decompressed, no bounding boxes)
        config = BabelDOCConfig(
            translator=translator,
            input_file=file_path,
            lang_in=translator.lang_in,
            lang_out=translator.lang_out,
            doc_layout_model=None,  # Use None like in the reference project
            output_dir=output_dir,
            debug=False,  # Explicitly disable debug mode to prevent bounding boxes
            # Additional parameters from the reference project
            font=None,
            pages=None,
            # Set to generate only monolingual PDF without dual version
            no_dual=True,      # Don't generate dual-language PDF
            no_mono=False,     # Generate monolingual PDF (the translated version)
            # Argos runs locally — there is no remote rate limit to respect,
            # and BabelDOC's RateLimiter would otherwise inject ~250ms of dead
            # time per paragraph. LLMs still need qps=4 to avoid 429s.
            qps=10_000 if is_argos else 4,
            formular_font_pattern=None,
            formular_char_pattern=None,
            split_short_lines=False,
            short_line_split_factor=0.8,
            disable_rich_text_translate=True,
            dual_translate_first=False,
            # `enhance_compatibility=True` forces extra typesetting passes
            # that double-render text. Argos doesn't need them; LLMs benefit
            # for tricky fonts.
            enhance_compatibility=not is_argos,
            use_alternating_pages_dual=False,
            # Set watermark mode to NoWatermark to avoid bounding boxes
            watermark_output_mode=BabelDOCWatermarkMode.NoWatermark if BabelDOCWatermarkMode else None,
            min_text_length=translation_settings.min_text_length,
            report_interval=0.1,
            skip_clean=True,
            split_strategy=None,
            table_model=None,
            # SSIM-based scanned-page detection renders every page twice.
            # The papers users translate offline are almost always
            # text-PDFs, so skip the detection for Argos.
            skip_scanned_detection=is_argos,
            ocr_workaround=False,
            custom_system_prompt=None,
            glossaries=None,
            auto_enable_ocr_workaround=False,
            pool_max_workers=processing_settings.max_workers,
            # Heaviest non-translation stage (weight 30 in BabelDOC). It
            # invokes the translator with extraction prompts — Argos is an
            # NMT model, can't follow those prompts, so the work is wasted.
            auto_extract_glossary=not is_argos,
            primary_font_family=None,
            only_include_translated_page=False,
            # Explicitly hide character boxes to prevent bounding boxes
            show_char_box=False,
        )
        
        logger.info("BabelDOC configuration created successfully")
        return config
    
    def _find_translated_file(self, output_dir: Path, original_stem: str) -> Optional[Path]:
        """Find translated PDF file in output directory."""
        # Streaming-render rolling output: highest-version wins. This is the
        # `{stem}_translated_v{N}.pdf` family written by `_rebuild_rolling_pdf`
        # after each chunk. The largest N is the latest, most-complete output.
        rolling = sorted(
            output_dir.glob(f"{original_stem}_translated_v*.pdf"),
            key=lambda p: int(p.stem.rsplit("_v", 1)[-1]) if p.stem.rsplit("_v", 1)[-1].isdigit() else -1,
        )
        if rolling:
            return rolling[-1]

        # Common patterns for translated files - prioritize mono version
        patterns = [
            f"{original_stem}_mono.pdf",  # Monolingual version (translated only)
            f"{original_stem}.vi.pdf",    # Language-specific naming
            f"{original_stem}_translated.pdf",
            f"{original_stem}.pdf"        # Generic PDF name
        ]
        
        for pattern in patterns:
            candidate = output_dir / pattern
            if candidate.exists():
                return candidate
        
        # If no specific pattern found, return the newest PDF in output dir
        pdf_files = list(output_dir.glob("*.pdf"))
        if pdf_files:
            # Sort by modification time, newest first
            pdf_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
            # Prefer files with 'mono' in the name
            for pdf_file in pdf_files:
                if 'mono' in pdf_file.name.lower():
                    return pdf_file
            # If no mono file found, return the newest
            return pdf_files[0]
        
        return None
    
    def cancel_processing(self):
        """Cancel current processing task."""
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
            logger.info(f"Cancelled processing session {self.session_id}")

    def get_partial_translated_file(self) -> Optional[Path]:
        """Latest rolling translated PDF written so far, or None if no chunk
        has finished. Used by the API layer to surface a partial result on
        cancellation — the viewer is already pointing at this file via the
        live chunk_ready stream, but the path needs to ride along on the
        terminal `cancelled` event too so the UI can label it as saved."""
        if self._output_dir is None or self._input_stem is None:
            return None
        return self._find_translated_file(self._output_dir, self._input_stem)

    def cleanup_partial_artifacts(self) -> None:
        """Remove older rolling-PDF versions, keeping only the latest as the
        user's partial result. Best-effort — file lock errors on Windows
        (PyMuPDF may still hold a handle from a worker thread that hasn't
        unwound yet) are logged, not raised."""
        if self._output_dir is None or self._input_stem is None:
            return
        versions = sorted(
            self._output_dir.glob(f"{self._input_stem}_translated_v*.pdf"),
            key=lambda p: int(p.stem.rsplit("_v", 1)[-1])
            if p.stem.rsplit("_v", 1)[-1].isdigit() else -1,
        )
        for stale in versions[:-1]:
            try:
                stale.unlink()
            except OSError as exc:
                logger.warning("Could not delete intermediate %s: %s", stale, exc)
    
    def get_session_info(self) -> Dict[str, Any]:
        """Get information about current processing session."""
        return {
            "session_id": self.session_id,
            "settings": self.settings.dict()
        }