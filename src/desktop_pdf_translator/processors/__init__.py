"""Core processing pipeline for PDF translation with BabelDOC integration.

Only free-to-import names are re-exported. `PDFProcessor` is not one of them:
it pulls in BabelDOC (~5 s), and this file runs whenever anything imports a
sibling module such as `processors.pdf_cache`. Import it from
`processors.processor` at the call site instead.
"""

from .events import (
    CompletionEvent,
    ErrorEvent,
    ProcessingEvent,
    ProgressEvent,
)
from .exceptions import (
    BabelDOCError,
    FileValidationError,
    ProcessingError,
    TranslationProcessError,
)

__all__ = [
    "ProcessingError",
    "BabelDOCError",
    "TranslationProcessError",
    "FileValidationError",
    "ProcessingEvent",
    "ProgressEvent",
    "ErrorEvent",
    "CompletionEvent",
]
