"""
Custom exceptions for PDF processing pipeline.
"""

from ..engine_assets import describe_asset_failure


class ProcessingError(Exception):
    """Base class for all processing-related errors."""
    
    def __init__(self, message: str, details: str = None):
        super().__init__(message)
        self.message = message
        self.details = details
    
    def __reduce__(self):
        """Support for pickling the exception when passing between processes."""
        return self.__class__, (self.message, self.details)


class BabelDOCError(ProcessingError):
    """Error originating from the BabelDOC library."""
    
    def __init__(self, message: str, original_error: Exception = None, details: str = None):
        super().__init__(message, details)
        self.original_error = original_error
    
    def __reduce__(self):
        """Support for pickling the exception when passing between processes."""
        return self.__class__, (self.message, self.original_error, self.details)
    
    def __str__(self):
        base_msg = super().__str__()
        if self.original_error:
            return f"{base_msg} - Original error: {self.original_error}"
        return base_msg


def babeldoc_chunk_error(index: int, error: BaseException) -> BabelDOCError:
    """The error a failed BabelDOC chunk becomes, given whatever it died of.

    BabelDOC's asset layer reports every failure by calling `exit(1)`, so what
    arrives here when a model or font can't be fetched is a bare `SystemExit(1)`
    — `str()` of which is ``1``. Handing that to the generic formatter produced
    the entire user-facing symptom of issue #21: "BabelDOC processing error in
    chunk 1: 1".

    `original_error` is deliberately left unset on that branch. `__str__`
    appends it and the job layer sends `str(exc)`, so passing the `SystemExit`
    through would staple the ``1`` back onto the end of the sentence.
    """
    if isinstance(error, SystemExit):
        return BabelDOCError(describe_asset_failure())
    return BabelDOCError(
        f"BabelDOC processing error in chunk {index}: {error}",
        original_error=error,
    )


class TranslationProcessError(ProcessingError):
    """Error occurring during the translation process."""
    
    def __init__(self, message: str, translator_name: str = None, details: str = None):
        super().__init__(message, details)
        self.translator_name = translator_name
    
    def __reduce__(self):
        """Support for pickling the exception when passing between processes."""
        return self.__class__, (self.message, self.translator_name, self.details)


class FileValidationError(ProcessingError):
    """Error during file validation."""
    
    def __init__(self, message: str, file_path: str = None, details: str = None):
        super().__init__(message, details)
        self.file_path = file_path
    
    def __reduce__(self):
        """Support for pickling the exception when passing between processes."""
        return self.__class__, (self.message, self.file_path, self.details)


class ConfigurationError(ProcessingError):
    """Error in processing configuration."""
    
    def __init__(self, message: str, config_section: str = None, details: str = None):
        super().__init__(message, details)
        self.config_section = config_section
    
    def __reduce__(self):
        """Support for pickling the exception when passing between processes."""
        return self.__class__, (self.message, self.config_section, self.details)


class ProcessingTimeoutError(ProcessingError):
    """Processing timeout error."""
    
    def __init__(self, message: str, timeout_seconds: float = None, details: str = None):
        super().__init__(message, details)
        self.timeout_seconds = timeout_seconds
    
    def __reduce__(self):
        """Support for pickling the exception when passing between processes."""
        return self.__class__, (self.message, self.timeout_seconds, self.details)