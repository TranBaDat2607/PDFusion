"""
Base translator interface compatible with BabelDOC.
"""

import logging
import re
import threading
from abc import ABC, abstractmethod
from typing import Dict, Optional, Any

from ..config import LanguageCode


logger = logging.getLogger(__name__)


LANGUAGE_DISPLAY_NAMES: Dict[str, str] = {
    "vi": "Vietnamese (Tiếng Việt)",
    "en": "English",
    "ja": "Japanese (日本語)",
    "zh-cn": "Simplified Chinese (简体中文)",
    "zh-tw": "Traditional Chinese (繁體中文)",
    "auto": "automatically detected language",
}


# Statuses that mean the credentials are wrong, not that the request was
# unlucky. Every remaining paragraph will fail identically, so the job should
# stop rather than quietly emit a source-text copy of the document.
_FATAL_STATUS_CODES = frozenset({401, 403})

# Fallback for SDK wrappers that don't expose a status: google-genai's
# ClientError, for one, puts the code in its message. Matched against
# `str(error)` — the exception's own text, never the document's.
#
# Every marker here names an API key specifically. Generic phrases like
# "unauthorized" or "permission denied" were tried and removed: a provider
# that means them also sends 401/403, which `_status_code_of` already catches,
# while `PermissionError`/`OSError` — a Windows file lock on the CTranslate2
# model, say — carry "Permission denied" in their text and would abort a run
# that had nothing wrong with its credentials.
_FATAL_MESSAGE_MARKERS = (
    "invalid_api_key",
    "invalid api key",
    "incorrect api key",
    "api key not valid",
    "authenticationerror",
)


def _status_code_of(error: BaseException) -> Optional[int]:
    """Best-effort HTTP status for an SDK exception.

    Duck-typed on purpose: this module must stay importable without the
    OpenAI / Anthropic / google-genai packages installed, so the exception
    classes can't be referenced by name. All three expose the status either
    directly or on a `.response`.
    """
    for attr in ("status_code", "code", "http_status"):
        value = getattr(error, attr, None)
        if isinstance(value, bool):  # bool is an int subclass — never a status
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    status = getattr(getattr(error, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


def is_fatal_translation_error(error: BaseException) -> bool:
    """Whether this failure means every later paragraph will fail the same way.

    A rejected key is the case that matters: BabelDOC logs and continues on a
    translator error, so without this a bad key produces a full-length,
    fully-untranslated PDF that reports success.
    """
    if _status_code_of(error) in _FATAL_STATUS_CODES:
        return True
    message = str(error).lower()
    return any(marker in message for marker in _FATAL_MESSAGE_MARKERS)


def describe_fatal_error(service_name: str, error: BaseException) -> str:
    """The sentence the user sees when a fatal failure stops a job.

    Argos gets its own wording. It is a fatal path — a blocked package index
    returns 403 from `_ensure_en_vi_installed`, which fails every paragraph
    identically — but it has no API key, so the LLM sentence would tell the
    user to check a credential they never set and to "switch to Argos" while
    already on it.
    """
    if service_name == "argos":
        return (
            f"Argos could not translate: {error}. The offline language pack "
            "may be missing or unreachable — check your connection, or add an "
            "API key in Settings to use an online translator."
        )
    return (
        f"{service_name} rejected the request: {error}. "
        "Check the API key in Settings, or switch to Argos (offline)."
    )


class BaseTranslator(ABC):
    """
    Base translator interface compatible with BabelDOC integration.
    
    This interface follows the BabelDOC specification for translator compatibility:
    - Must implement translate() method accepting a single text string
    - Must support formula placeholder handling
    - Must support language attributes: lang_in, lang_out
    """

    def __init__(self, lang_in: str, lang_out: str, **kwargs):
        """Initialize translator with language configuration.

        Args:
            lang_in: Source language code
            lang_out: Target language code
            **kwargs: Additional translator-specific configuration. Recognized
                across all backends:
                  on_paragraph_translated: Optional[Callable[[str, str], None]]
                    Fired (from whatever thread translate() runs on) after
                    each paragraph is translated. Receives (source, target).
                    Used by the processor to emit `paragraph_translated`
                    SSE events for the live ticker UI.
                  on_translation_failed: Optional[Callable[[BaseException, bool], None]]
                    Fired on the same threads when a paragraph could not be
                    translated. Receives (error, fatal), where `fatal` marks a
                    failure that will repeat for every remaining paragraph
                    (see `is_fatal_translation_error`). The processor counts
                    these and aborts the job on a fatal one.
        """
        self.lang_in = self._normalize_language_code(lang_in)
        self.lang_out = self._normalize_language_code(lang_out)
        # Both counters are read by the processor: `translate_call_count` is
        # the denominator of the "N of M paragraphs could not be translated"
        # banner, `failed_translations` the numerator and the reason a run is
        # kept out of the PDF cache. The translator itself never acts on them.
        #
        # One instance is shared across BabelDOC's whole worker pool, so both
        # increments are locked — `+= 1` is a read-modify-write and would drop
        # counts under exactly the concurrency this exists to measure.
        self._counter_lock = threading.Lock()
        self.translate_call_count = 0
        self.failed_translations = 0

        # Pop the cross-cutting callbacks before passing the rest to the
        # subclass setup so backends don't need to thread them through their
        # own kwargs handling.
        self._on_paragraph_translated = kwargs.pop(
            "on_paragraph_translated", None
        )
        self._on_translation_failed = kwargs.pop("on_translation_failed", None)

        # Initialize translator-specific settings
        self._setup_translator(**kwargs)

        logger.info(f"Initialized {self.__class__.__name__} translator: {self.lang_in} -> {self.lang_out}")

    def _fire_paragraph_callback(self, source: str, target: str) -> None:
        """Best-effort: invoke the on_paragraph_translated callback if set.
        Any exception in the callback is swallowed — we never want a UI hook
        to break a translation."""
        cb = self._on_paragraph_translated
        if cb is None:
            return
        try:
            cb(source, target)
        except Exception:
            logger.debug("on_paragraph_translated callback raised", exc_info=True)

    def _note_translate_call(self) -> int:
        """Count one `translate()` entry and return the new total.

        Every backend calls this on the first line of `translate()`, so the
        total covers each unit BabelDOC handed over — including the ones that
        short-circuit on empty text, which is what makes it a usable
        denominator for the failure banner.
        """
        with self._counter_lock:
            self.translate_call_count += 1
            return self.translate_call_count

    def _fire_failure_callback(self, error: BaseException, fatal: bool) -> None:
        """Best-effort: report a failed paragraph. Same contract as
        `_fire_paragraph_callback` — a UI hook must never break a run."""
        cb = self._on_translation_failed
        if cb is None:
            return
        try:
            cb(error, fatal)
        except Exception:
            logger.debug("on_translation_failed callback raised", exc_info=True)
    
    def _normalize_language_code(self, lang_code: str) -> str:
        """Normalize language code for translator compatibility."""
        # Map our enum values to common formats
        lang_map = {
            LanguageCode.VIETNAMESE: "vi",
            LanguageCode.ENGLISH: "en", 
            LanguageCode.JAPANESE: "ja",
            LanguageCode.CHINESE_SIMPLIFIED: "zh-cn",
            LanguageCode.CHINESE_TRADITIONAL: "zh-tw",
            LanguageCode.AUTO: "auto"
        }
        
        return lang_map.get(lang_code, lang_code)
    
    @abstractmethod
    def _setup_translator(self, **kwargs):
        """Setup translator-specific configuration."""
        pass
    
    @abstractmethod
    def translate(self, text: str) -> str:
        """
        Translate the given text.
        
        This is the main interface method that BabelDOC will call.
        Must accept a single text string and return a single translated string.
        
        Args:
            text: Text to translate
            
        Returns:
            Translated text
        """
        pass

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 1000,
    ) -> Optional[str]:
        """Freeform text generation for RAG answer synthesis.

        LLM backends override this. The base implementation returns None,
        meaning the backend (e.g. Argos NMT) cannot follow instructions —
        callers must fall back to a non-LLM path.
        """
        return None


    def get_formular_placeholder(self, placeholder_id: int) -> tuple[str, str]:
        """
        Get formula placeholder for protecting math content.
        
        BabelDOC uses this for formula preservation during translation.
        
        Args:
            placeholder_id: Unique identifier for the placeholder
            
        Returns:
            Tuple of (placeholder_text, regex_pattern)
        """
        placeholder = f"{{v{placeholder_id}}}"
        regex_pattern = f"{{\\s*v\\s*{placeholder_id}\\s*}}"
        return placeholder, regex_pattern
    
    def get_rich_text_left_placeholder(self, placeholder_id: int | str):
        return f"<b{placeholder_id}>"

    def get_rich_text_right_placeholder(self, placeholder_id: int | str):
        return f"</b{placeholder_id}>"
    
    def restore_formular_placeholder(self, text: str, placeholder_id: int, original_formula: str) -> str:
        """
        Restore formula placeholder with original content.
        
        Args:
            text: Text containing placeholder
            placeholder_id: Placeholder identifier
            original_formula: Original formula content to restore
            
        Returns:
            Text with restored formula
        """
        placeholder, regex_pattern = self.get_formular_placeholder(placeholder_id)
        # The formula is literal text, not a regex replacement template — a
        # lambda keeps backslashes/`\1` in it from being interpreted by re.sub.
        return re.sub(
            regex_pattern, lambda _m: original_formula, text, flags=re.IGNORECASE
        )

    def _preprocess_text(self, text: str) -> str:
        """Preprocess text before translation."""
        # Basic text cleaning
        text = text.strip()
        
        # Handle Vietnamese-specific preprocessing
        if self.lang_out == "vi":
            # Add Vietnamese-specific text normalization here if needed
            pass
        
        return text
    
    def _postprocess_text(self, text: str) -> str:
        """Postprocess translated text."""
        text = text.strip()

        if self.lang_out == "vi":
            text = re.sub(r'\s+([.,;:!?])', r'\1', text)
            # Insert a space after punctuation only at genuine word boundaries.
            # `,;:` → only when followed by a letter, so `1,000`, `12:30`,
            # and `http://` stay intact. Sentence enders `.!?` → only before
            # an uppercase letter (sentence boundary), so `3.14`,
            # `example.com`, and `?a=1` query strings stay intact.
            text = re.sub(r'([,;:])(?=[^\W\d_])', r'\1 ', text)
            text = re.sub(r'([.!?])(?=[A-Z])', r'\1 ', text)
            text = re.sub(r' {2,}', ' ', text)

        return text
    
    def _handle_translation_error(self, error: Exception, text: str) -> str:
        """Record a failed paragraph and fall back to its source text.

        The fallback stays: BabelDOC's `ILTranslator` catches whatever
        `translate()` raises and continues anyway, so raising here would only
        lose the paragraph's text without stopping anything. What changes is
        that the failure is now *counted* and *reported* — the processor uses
        that to refuse caching the result, to tell the user how much of the
        document is untranslated, and to abort outright on a rejected key.
        """
        with self._counter_lock:
            self.failed_translations += 1
            failure_number = self.failed_translations
        fatal = is_fatal_translation_error(error)
        logger.error(
            "Translation failed (%s#%d, fatal=%s) for text: %s..., Error: %s",
            self.__class__.__name__,
            failure_number,
            fatal,
            text[:100],
            error,
        )
        self._fire_failure_callback(error, fatal)

        # Return original text as fallback
        return text
    
    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.lang_in} -> {self.lang_out})"
    
    def __repr__(self) -> str:
        return self.__str__()