"""
Main PDF processing pipeline with BabelDOC integration and Vietnamese optimization.
"""

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import AsyncGenerator, Optional, Dict, Any

import fitz  # PyMuPDF

try:
    from babeldoc.format.pdf.high_level import async_translate as babeldoc_translate
    from babeldoc.format.pdf.translation_config import TranslationConfig as BabelDOCConfig
    from babeldoc.format.pdf.translation_config import WatermarkOutputMode as BabelDOCWatermarkMode
    BABELDOC_AVAILABLE = True
    logging.info("BabelDOC successfully imported")
except ImportError as e:
    BABELDOC_AVAILABLE = False
    babeldoc_translate = None
    BabelDOCConfig = None
    BabelDOCWatermarkMode = None
    logging.warning(f"BabelDOC import failed: {e}")

from ..config import get_settings, FileMetadata, LanguageCode, TranslationService
from ..translators import TranslatorFactory
from .events import ProcessingEvent, ProgressEvent, ErrorEvent, CompletionEvent, EventType
from .exceptions import ProcessingError, BabelDOCError, FileValidationError, ConfigurationError


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
        
        if not BABELDOC_AVAILABLE:
            logger.warning("BabelDOC is not available. Some features may be limited.")
    
    async def process_pdf(
        self, 
        file_path: Path,
        source_lang: Optional[LanguageCode] = None,
        target_lang: Optional[LanguageCode] = None,
        translation_service: Optional[TranslationService] = None,
        output_dir: Optional[Path] = None
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
            
            # Log the actual languages being used
            logger.info(f"Using languages - Source: {source_lang}, Target: {target_lang}")
            
            # Set output directory
            if output_dir is None:
                output_dir = Path.cwd() / "translated_pdfs"
            output_dir.mkdir(parents=True, exist_ok=True)
            
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
            
            translator = TranslatorFactory.create_translator(
                service=translation_service,
                lang_in=source_lang,
                lang_out=target_lang
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
            
            # Step 3: BabelDOC processing
            if BABELDOC_AVAILABLE:
                logger.info("BABELDOC_AVAILABLE is True, using BabelDOC processing")
                async for event in self._process_with_babeldoc(
                    file_path, translator, output_dir, file_metadata
                ):
                    yield event
            else:
                logger.warning(f"BABELDOC_AVAILABLE is False ({BABELDOC_AVAILABLE}), using fallback processing")
                # Fallback processing without BabelDOC
                async for event in self._process_without_babeldoc(
                    file_path, translator, output_dir, file_metadata
                ):
                    yield event
            
            # Step 4: Completion
            processing_time = time.time() - start_time
            
            # Find output files
            translated_file = self._find_translated_file(output_dir, file_path.stem)
            
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
        file_metadata: FileMetadata
    ) -> AsyncGenerator[ProcessingEvent, None]:
        """Process PDF using BabelDOC pipeline."""
        try:
            logger.info("Starting BabelDOC processing")
            
            # Create BabelDOC configuration
            babeldoc_config = self._create_babeldoc_config(
                file_path, translator, output_dir
            )
            
            logger.info("BabelDOC config created, starting translation")
            
            yield ProgressEvent(
                type=EventType.PROGRESS_UPDATE,
                timestamp=time.time(),
                session_id=self.session_id,
                data={},
                stage="Starting BabelDOC translation",
                current_step=3,
                total_steps=4,
                progress_percent=50.0,
                message="Initializing BabelDOC processing"
            )
            
            # Process with BabelDOC - directly iterate over the async generator
            async for event in babeldoc_translate(babeldoc_config):
                logger.info(f"BabelDOC event: {event}")
                # Convert BabelDOC events to our event format
                if event["type"] == "progress_update":
                    yield ProgressEvent(
                        type=EventType.PROGRESS_UPDATE,
                        timestamp=time.time(),
                        session_id=self.session_id,
                        data={},
                        stage=event.get("stage", "Processing"),
                        current_step=3,
                        total_steps=4,
                        progress_percent=50.0 + (event.get("overall_progress", 0) * 0.4),
                        message=event.get("message", "")
                    )
                elif event["type"] == "error":
                    logger.error(f"BabelDOC error: {event}")
                    raise BabelDOCError(
                        message=f"BabelDOC processing failed: {event.get('error', 'Unknown error')}",
                        details=event.get("details")
                    )
                elif event["type"] == "finish":
                    logger.info("BabelDOC processing completed successfully")
                    yield ProgressEvent(
                        type=EventType.PROGRESS_UPDATE,
                        timestamp=time.time(),
                        session_id=self.session_id,
                        data={},
                        stage="BabelDOC processing complete",
                        current_step=3,
                        total_steps=4,
                        progress_percent=90.0,
                        message="Translation completed successfully"
                    )
                    break
            
        except BabelDOCError:
            raise
        except Exception as e:
            logger.exception(f"BabelDOC processing error: {e}")
            raise BabelDOCError(f"BabelDOC processing error: {e}", original_error=e)
    
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
        if not BABELDOC_AVAILABLE or not BabelDOCConfig:
            raise ConfigurationError("BabelDOC is not available")
        
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
            qps=4,  # Default QPS limit
            formular_font_pattern=None,
            formular_char_pattern=None,
            split_short_lines=False,
            short_line_split_factor=0.8,
            disable_rich_text_translate=False,
            dual_translate_first=False,
            enhance_compatibility=False,
            use_alternating_pages_dual=False,
            # Set watermark mode to NoWatermark to avoid bounding boxes
            watermark_output_mode=BabelDOCWatermarkMode.NoWatermark if BabelDOCWatermarkMode else None,
            min_text_length=translation_settings.min_text_length,
            report_interval=0.1,
            skip_clean=False,
            split_strategy=None,
            table_model=None,
            skip_scanned_detection=False,
            ocr_workaround=False,
            custom_system_prompt=None,
            glossaries=None,
            auto_enable_ocr_workaround=False,
            pool_max_workers=processing_settings.max_workers,
            auto_extract_glossary=True,
            primary_font_family=None,
            only_include_translated_page=False,
            # Explicitly hide character boxes to prevent bounding boxes
            show_char_box=False,
        )
        
        logger.info("BabelDOC configuration created successfully")
        return config
    
    def _find_translated_file(self, output_dir: Path, original_stem: str) -> Optional[Path]:
        """Find the translated output file."""
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
    
    def get_session_info(self) -> Dict[str, Any]:
        """Get information about current processing session."""
        return {
            "session_id": self.session_id,
            "babeldoc_available": BABELDOC_AVAILABLE,
            "settings": self.settings.dict()
        }