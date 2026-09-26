"""
Base translator interface compatible with BabelDOC.
"""

import logging
import random
import re
import threading
import time
from abc import ABC, abstractmethod
from functools import partial
from typing import Callable, Dict, FrozenSet, Optional, Any, Sequence

from ..config import LanguageCode
from . import param_compat
from .rate_limiter import default_qps_for, get_rate_limiter


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


# Transport failures worth another attempt. With `max_retries=0` on the SDK
# clients these carry no status code, so they are the whole signal for what
# the SDKs used to retry themselves.
#
# Matched by class *name* along the MRO — never `isinstance`, because this
# module has to import with none of the provider SDKs (nor httpx) installed,
# so the classes cannot be referenced. Two base names cover every case, and
# walking the MRO rather than testing the concrete class is what makes that
# true:
#
#   APIConnectionError — openai and anthropic funnel every transport failure
#     into it (their `except Exception` fallback in `_base_client`), and both
#     SDKs' `APITimeoutError` subclasses it.
#   TransportError — google-genai does *not* wrap, so raw httpx exceptions
#     arrive here. ConnectError, ReadError, WriteError, NetworkError,
#     ProxyError, CloseError, RemoteProtocolError and the four Timeout
#     classes all derive from it.
#
# Listing the leaf names instead missed the five httpx classes with no
# "Timeout"/"Connect" in their name, which on Gemini meant a socket-level
# read failure was still an immediately-lost paragraph.
_RETRYABLE_ERROR_NAMES = frozenset({"APIConnectionError", "TransportError"})


def is_retryable_translation_error(error: BaseException) -> bool:
    """A 429, 5xx, or a transient connection/timeout failure — worth one
    more attempt after a backoff. The status range (not a fixed set) also
    catches Anthropic's 529 "overloaded"; 408/409 are included alongside
    429 since some providers use them for the same "try again" meaning."""
    if any(cls.__name__ in _RETRYABLE_ERROR_NAMES for cls in type(error).__mro__):
        return True
    status = _status_code_of(error)
    if status is None:
        return False
    return status in (408, 409, 429) or 500 <= status < 600


class TranslationCancelled(Exception):
    """The job's cancel flag fired while `_call_with_backoff` was waiting on
    the rate limiter or a retry backoff. Not a translation failure."""


_MAX_RETRIES = 6
_RETRY_BASE_DELAY_S = 1.0
_RETRY_MAX_DELAY_S = 20.0


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

    # Overridden by LLM backends (a plain class attribute, or set as
    # self._SERVICE_NAME inside _setup_translator for a per-instance value).
    # None means "no shared rate limiter" — Argos and any non-LLM backend.
    _SERVICE_NAME: Optional[str] = None

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
                  cancel_event: Optional[threading.Event]
                    Checked at the top of translate(); when set, the
                    translator returns source text without calling out.
                  max_qps: Optional[float]
                    Overrides the shared rate limiter's default for this
                    backend's service. None keeps the built-in default.
                  provider_id: Optional[str]
                    The registry provider this instance serves, which names
                    its rate limiter. One class serves several providers —
                    OpenRouter, DeepSeek and Ollama all build
                    `OpenAITranslator` — and keyed on the class's own name
                    they drew on OpenAI's budget (#88).
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
        self.retry_count = 0

        # Pop the cross-cutting callbacks before passing the rest to the
        # subclass setup so backends don't need to thread them through their
        # own kwargs handling.
        self._on_paragraph_translated = kwargs.pop(
            "on_paragraph_translated", None
        )
        self._on_translation_failed = kwargs.pop("on_translation_failed", None)
        self._cancel_event: Optional[threading.Event] = kwargs.pop(
            "cancel_event", None
        )
        self.max_qps: Optional[float] = kwargs.pop("max_qps", None)
        provider_id: Optional[str] = kwargs.pop("provider_id", None)
        if provider_id is not None and self._SERVICE_NAME is not None:
            self._SERVICE_NAME = provider_id

        # Initialize translator-specific settings
        self._setup_translator(**kwargs)

        # Applied once at construction, not per-call: max_qps is fixed for
        # this instance's lifetime, so re-applying it on every translate()
        # would just take the limiter's lock to write the same value back.
        #
        # Applied unconditionally, resolving None to the built-in default,
        # because the limiter outlives the translator. Skipping the call when
        # max_qps is None would let a *cleared* override survive: raise the
        # rate once, delete the line from config.toml, and the sidecar keeps
        # running at the raised rate until it restarts.
        if self._SERVICE_NAME is not None:
            get_rate_limiter(
                self._SERVICE_NAME,
                qps=(
                    self.max_qps
                    if self.max_qps is not None
                    else default_qps_for(self._SERVICE_NAME)
                ),
            )

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

    def is_cancelled(self) -> bool:
        return self._cancel_event is not None and self._cancel_event.is_set()

    def _sleep_or_cancel(self, seconds: float) -> bool:
        """Sleep up to `seconds`, waking early if cancelled. Returns True if
        cancelled during the sleep."""
        if self._cancel_event is None:
            time.sleep(seconds)
            return False
        return self._cancel_event.wait(timeout=seconds)

    def _call_with_backoff(self, request: Callable[[], Any]) -> Any:
        """Run a blocking SDK call behind self._SERVICE_NAME's shared rate
        limiter, retrying on 429/5xx/timeout/connection errors with jittered
        exponential backoff. Fatal errors (401/403) and anything else
        propagate immediately.

        Raises TranslationCancelled if the job's cancel flag fires while
        waiting on the limiter or a backoff; re-raises the last error once
        the retry budget is exhausted.
        """
        # `_SERVICE_NAME is None` means this backend opts out of the shared
        # limiter (Argos, and anything else non-LLM). It has to be handled
        # here rather than passed through: `get_rate_limiter(None)` would
        # cheerfully build a real `None`-keyed bucket at the fallback rate and
        # share it between every backend that never named a service.
        limiter = (
            get_rate_limiter(self._SERVICE_NAME)
            if self._SERVICE_NAME is not None
            else None
        )
        attempt = 0
        while True:
            if limiter is None:
                # No token to wait on, so check the flag the acquire would
                # otherwise have observed.
                if self.is_cancelled():
                    raise TranslationCancelled()
            elif not limiter.acquire(cancel_event=self._cancel_event):
                raise TranslationCancelled()
            try:
                return request()
            except Exception as error:
                if self.is_cancelled():
                    raise TranslationCancelled() from error
                if is_fatal_translation_error(error) or not is_retryable_translation_error(error):
                    raise
                attempt += 1
                if attempt > _MAX_RETRIES:
                    raise
                delay = min(_RETRY_BASE_DELAY_S * (2 ** (attempt - 1)), _RETRY_MAX_DELAY_S)
                delay = delay / 2 + random.uniform(0, delay / 2)
                with self._counter_lock:
                    self.retry_count += 1
                logger.warning(
                    "%s request failed (attempt %d/%d), retrying in %.1fs: %s",
                    self._SERVICE_NAME, attempt, _MAX_RETRIES, delay, error,
                )
                if self._sleep_or_cancel(delay):
                    raise TranslationCancelled() from error

    def _endpoint_key(self) -> param_compat.EndpointKey:
        """Which server and model this translator's requests go to."""
        return (
            self._SERVICE_NAME or type(self).__name__,
            getattr(self, "base_url", None),
            getattr(self, "model", None) or "",
        )

    def _call_adapting(
        self,
        send: Callable[[FrozenSet[str]], Any],
        adaptable: Sequence[str],
        *,
        backoff: bool = True,
    ) -> Any:
        """Make a request, leaving out what this endpoint said it won't take.

        `send(rejected)` builds and sends the request without the parameters
        in `rejected`. A 400 refusing one of `adaptable` is remembered for this
        endpoint and model (`param_compat`), and the request goes again without
        it. Every other failure propagates as it came. Each parameter is left
        out at most once, so this ends.

        `backoff=False` sends directly, for `validate_configuration`, which has
        never gone through the rate limiter. It still has to send what
        `translate` sends, or "valid" would vouch for a request nothing makes.
        """
        key = self._endpoint_key()
        while True:
            rejected = param_compat.rejected_params(key)
            request = partial(send, rejected)
            try:
                return self._call_with_backoff(request) if backoff else request()
            except Exception as error:
                param = param_compat.rejected_param(
                    _status_code_of(error),
                    str(error),
                    [name for name in adaptable if name not in rejected],
                )
                if param is None:
                    raise
                param_compat.remember_rejected(key, param)
                logger.info(
                    "%s model %r does not take %r; sending without it from now on",
                    key[0], key[2], param,
                )

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