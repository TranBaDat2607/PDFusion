"""
Worker thread for handling translation processing.
"""

import logging
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any

from PySide6.QtCore import QThread, Signal

from ..config import LanguageCode, TranslationService
from ..processors import PDFProcessor
from ..processors.events import EventType


logger = logging.getLogger(__name__)


class TranslationWorker(QThread):
    """
    Worker thread for handling PDF translation processing.
    
    This thread runs the async translation pipeline and emits signals
    for progress updates and completion.
    """
    
    progress_updated = Signal(dict)  # Progress event data
    translation_completed = Signal(dict)  # Completion data
    translation_failed = Signal(str)  # Error message
    
    def __init__(
        self,
        file_path: Path,
        source_lang: str = "auto", 
        target_lang: str = "vi",
        service: str = "openai",
        output_dir: Optional[Path] = None,
        **kwargs
    ):
        super().__init__()
        
        self.file_path = file_path
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.service = service
        self.output_dir = output_dir
        self.kwargs = kwargs
        
        self.processor = PDFProcessor()
        self._cancelled = False
    
    def run(self):
        """Main worker thread execution."""
        try:
            # Create new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                # Run the async translation process
                loop.run_until_complete(self._run_translation())
            finally:
                # Clean up pending tasks before closing the loop
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                
                loop.close()
                
        except Exception as e:
            logger.exception(f"Worker thread error: {e}")
            self.translation_failed.emit(str(e))
    
    async def _run_translation(self):
        """Run the async translation process."""
        try:
            # Convert string language codes to enum values
            source_lang = self._parse_language_code(self.source_lang)
            target_lang = self._parse_language_code(self.target_lang)
            service = self._parse_translation_service(self.service)
            
            # Log the parsed languages
            logger.info(f"Parsed languages - Source: {source_lang}, Target: {target_lang}")
            
            # Process PDF with progress tracking
            async for event in self.processor.process_pdf(
                file_path=self.file_path,
                source_lang=source_lang,
                target_lang=target_lang,
                translation_service=service,
                output_dir=self.output_dir
            ):
                # Check for cancellation
                if self._cancelled:
                    logger.info("Translation cancelled by user")
                    # Cancel the processor's processing
                    self.processor.cancel_processing()
                    return
                
                # Emit appropriate signal based on event type
                event_data = event.to_dict()
                
                if event.type in [EventType.PROGRESS_START, EventType.PROGRESS_UPDATE, EventType.PROGRESS_END]:
                    self.progress_updated.emit(event_data)
                
                elif event.type == EventType.FINISH:
                    self.translation_completed.emit(event_data)
                    return
                
                elif event.type == EventType.ERROR:
                    error_msg = event_data.get('error_message', 'Unknown error')
                    self.translation_failed.emit(error_msg)
                    return
            
        except asyncio.CancelledError:
            logger.info("Translation task was cancelled")
            return
        
        except Exception as e:
            logger.exception(f"Translation process error: {e}")
            self.translation_failed.emit(f"Translation failed: {e}")
    
    def _parse_language_code(self, lang_code: str) -> LanguageCode:
        """Parse language code from string."""
        lang_map = {
            "auto": LanguageCode.AUTO,
            "vi": LanguageCode.VIETNAMESE,
            "en": LanguageCode.ENGLISH,
            "ja": LanguageCode.JAPANESE,
            "zh-cn": LanguageCode.CHINESE_SIMPLIFIED,
            "zh-tw": LanguageCode.CHINESE_TRADITIONAL
        }
        
        return lang_map.get(lang_code.lower(), LanguageCode.AUTO)
    
    def _parse_translation_service(self, service: str) -> TranslationService:
        """Parse translation service from string."""
        service_map = {
            "openai": TranslationService.OPENAI,
            "gemini": TranslationService.GEMINI
        }
        
        return service_map.get(service.lower(), TranslationService.OPENAI)
    
    def cancel(self):
        """Cancel the translation process."""
        self._cancelled = True
        
        # Cancel the processor if it has a running task
        if self.processor:
            self.processor.cancel_processing()
        
        # Request thread interruption
        self.requestInterruption()
        
        logger.info("Translation cancellation requested")