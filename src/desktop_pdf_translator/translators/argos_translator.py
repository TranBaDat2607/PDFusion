"""Argos Translate (offline NMT) backend — free, no API key.

Used as the default backend when no LLM key is configured. Supports
English → Vietnamese only in this MVP; other source languages raise
ValueError directing the user to switch source language or use an LLM.

The argostranslate package is imported lazily inside the methods that need
it, so sidecar startup time stays unaffected for users on LLM and the
~80 MB language pack downloads only on first translate.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from .base import BaseTranslator
from .translation_cache import llm_cache_get as _llm_cache_get, llm_cache_set as _llm_cache_set


logger = logging.getLogger(__name__)


_SUPPORTED_PAIRS = {("en", "vi")}

# Concurrent paragraph workers must not all attempt to download the pack.
_install_lock = threading.Lock()
_en_vi_ready = False

# (processed_text, original_text, event, result_slot)
_BatchEntry = tuple[str, str, threading.Event, list]


def _preview(s: str, n: int = 60) -> str:
    """Single-line truncated preview for log lines."""
    s = (s or "").replace("\n", " ").replace("\r", " ").strip()
    return (s[:n] + "…") if len(s) > n else s


def _ensure_en_vi_installed() -> None:
    """Install the en→vi Argos package if not already present.

    Idempotent and thread-safe. Network access only on first call when no
    pack is on disk; afterwards O(1).
    """
    global _en_vi_ready
    if _en_vi_ready:
        return
    with _install_lock:
        if _en_vi_ready:
            return
        import argostranslate.package
        import argostranslate.translate

        installed = argostranslate.translate.get_installed_languages()
        from_lang = next((l for l in installed if l.code == "en"), None)
        to_lang = next((l for l in installed if l.code == "vi"), None)
        if from_lang and to_lang and from_lang.get_translation(to_lang) is not None:
            _en_vi_ready = True
            return

        logger.info("Argos en→vi pack not installed — downloading (~80 MB)")
        argostranslate.package.update_package_index()
        available = argostranslate.package.get_available_packages()
        pkg = next(
            (p for p in available if p.from_code == "en" and p.to_code == "vi"),
            None,
        )
        if pkg is None:
            raise RuntimeError(
                "Argos package index has no en→vi entry — check network connectivity"
            )
        argostranslate.package.install_from_path(pkg.download())
        logger.info("Argos en→vi pack installed")
        _en_vi_ready = True


class ArgosTranslator(BaseTranslator):
    """Offline NMT translator using Argos Translate (CTranslate2 backend).

    No API key. Bundled language pack downloads on first use (~80 MB).
    Supports `en → vi` only in this MVP — other source languages raise
    ValueError.
    """

    def __init__(self, lang_in: str, lang_out: str, **kwargs):
        super().__init__(lang_in, lang_out, **kwargs)

    def _setup_translator(self, **kwargs):
        # Argos has no API key, no model selection, no temperature. We accept
        # and ignore arbitrary kwargs so the factory's per-service config dict
        # (empty for ARGOS) stays uniform with the LLMs.
        self.model = kwargs.get("model", "argostranslate")

        # Streaming/batching: BabelDOC's worker pool calls translate() from up
        # to `pool_max_workers` threads concurrently. Instead of each thread
        # racing the same local CTranslate2 model, we coalesce them: enqueue,
        # wait for the batch to fill (or for the tail-timer to fire on the
        # last paragraphs of the document), then translate the whole batch
        # sequentially in one thread. With BabelDOC's default 4 workers and
        # batch_size=4, paragraphs naturally arrive in groups of 4.
        self.batch_size = max(1, int(kwargs.get("batch_size", 4)))
        self.batch_timeout = float(kwargs.get("batch_timeout", 2.0))
        self._batch_lock = threading.Lock()
        self._pending: list[_BatchEntry] = []
        self._flush_timer: threading.Timer | None = None
        self._batch_counter = 0

        # Pipeline batches across 2 workers: while one thread is mid-inference
        # on batch N, the other can start batch N+1's pre-processing /
        # tokenization. CTranslate2 serializes the actual decode internally,
        # but this overlapping cuts the inter-batch gap. ~15-30% throughput
        # win on typical academic PDFs.
        self._batch_executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="argos-batch"
        )

        # Argos has no built-in language detection. The default source in this
        # app is "auto" — without this normalization every translation against
        # Argos defaults would error out. Treat "auto" as English (the dominant
        # case for the academic-PDF use case). Users who actually have a
        # non-English source must select it explicitly in the toolbar; those
        # non-en pairs still raise below.
        if self.lang_in == "auto":
            logger.info(
                "ArgosTranslator: treating source 'auto' as 'en' (no language detection)"
            )
            self.lang_in = "en"

        if (self.lang_in, self.lang_out) not in _SUPPORTED_PAIRS:
            logger.warning(
                "ArgosTranslator constructed with unsupported pair %s→%s; "
                "translate() calls will raise.",
                self.lang_in,
                self.lang_out,
            )
        logger.info(
            "ArgosTranslator configured: %s -> %s (batch_size=%d, batch_timeout=%.1fs)",
            self.lang_in, self.lang_out, self.batch_size, self.batch_timeout,
        )

    def translate(self, text: str, **kwargs) -> str:
        self.translate_call_count += 1

        if (self.lang_in, self.lang_out) not in _SUPPORTED_PAIRS:
            raise ValueError(
                f"Argos Translate supports only English → Vietnamese in this "
                f"build (requested {self.lang_in} → {self.lang_out}). Switch "
                f"the source language to English in the toolbar, or paste an "
                f"LLM API key in Settings to use a paid translator."
            )

        processed = self._preprocess_text(text)
        if not processed.strip():
            return text

        cached = _llm_cache_get(processed, "en", "vi", "argos", self.model)
        if cached is not None:
            self._fire_paragraph_callback(processed, cached)
            return cached

        # Pay the ~80 MB language-pack download (if needed) BEFORE entering
        # the batching path. Otherwise the first thread to flush a batch
        # blocks inside _ensure_en_vi_installed while other threads pile
        # additional paragraphs into the queue and the tail-timer may fire
        # against an unfinished install.
        _ensure_en_vi_installed()

        event = threading.Event()
        result_slot: list[str | None] = [None]
        entry: _BatchEntry = (processed, text, event, result_slot)

        batch_to_flush: list[_BatchEntry] | None = None
        batch_id = 0
        with self._batch_lock:
            self._pending.append(entry)
            self._cancel_timer_locked()
            if len(self._pending) >= self.batch_size:
                batch_to_flush, batch_id = self._take_batch_locked()
            else:
                # (Re)arm the tail-flush timer so the last <batch_size
                # paragraphs of the document don't hang their callers.
                t = threading.Timer(self.batch_timeout, self._timed_flush)
                t.daemon = True
                self._flush_timer = t
                t.start()

        if batch_to_flush is not None:
            # Dispatch to a worker so the *caller* doesn't block this batch's
            # decode — it only blocks until its own entry's event fires, which
            # may happen while the next caller is already filling batch N+1.
            self._batch_executor.submit(
                self._translate_batch, batch_to_flush, batch_id
            )

        event.wait()
        return result_slot[0]

    def _cancel_timer_locked(self) -> None:
        """Cancel any armed tail-flush timer. Caller must hold _batch_lock."""
        if self._flush_timer is not None:
            self._flush_timer.cancel()
            self._flush_timer = None

    def _take_batch_locked(self) -> tuple[list[_BatchEntry], int]:
        """Snapshot+clear _pending and bump the batch counter. Caller must
        hold _batch_lock."""
        batch = self._pending[:]
        self._pending.clear()
        batch_id = self._batch_counter
        self._batch_counter += 1
        return batch, batch_id

    def _timed_flush(self) -> None:
        """Flush whatever is pending. Fires when the queue hasn't reached
        `batch_size` within `batch_timeout` seconds (i.e. document tail)."""
        with self._batch_lock:
            self._flush_timer = None
            if not self._pending:
                return
            batch_to_flush, batch_id = self._take_batch_locked()
        # Same dispatch path as the size-triggered flush so timer-driven tail
        # batches don't block the timer thread.
        self._batch_executor.submit(self._translate_batch, batch_to_flush, batch_id)

    def _translate_batch(self, batch: list[_BatchEntry], batch_id: int) -> None:
        """Translate every entry in `batch` sequentially, set its event."""
        import argostranslate.translate

        logger.info(
            "Argos streaming batch #%d: translating %d paragraph(s)",
            batch_id, len(batch),
        )
        for idx, (processed, original, event, result_slot) in enumerate(batch):
            try:
                translated = argostranslate.translate.translate(processed, "en", "vi")
                result = self._postprocess_text(translated)
                _llm_cache_set(processed, result, "en", "vi", "argos", self.model)
                result_slot[0] = result
                # Fire the live-ticker callback. Runs on the batch-executor
                # thread; the processor's callback bridges back to the event
                # loop via call_soon_threadsafe.
                self._fire_paragraph_callback(processed, result)
                logger.info(
                    "  [%d.%d] EN: %s | VI: %s",
                    batch_id, idx, _preview(processed), _preview(result),
                )
            except Exception as e:
                # Preserve the pre-streaming fail-soft behavior: a model
                # error returns the original paragraph instead of breaking
                # the whole PDF.
                result_slot[0] = self._handle_translation_error(e, original)
            finally:
                event.set()

    def close(self) -> None:
        """Release the batch executor. Safe to call multiple times."""
        try:
            self._batch_executor.shutdown(wait=False, cancel_futures=False)
        except Exception:
            pass

    def __del__(self) -> None:
        # Best-effort cleanup; ignore any teardown ordering issues.
        try:
            self.close()
        except Exception:
            pass

    def get_formular_placeholder(self, placeholder_id: int) -> tuple[str, str]:
        # NMT models don't follow prompt instructions, so the default {v1}-
        # style placeholder can be tokenized and split. Rare unicode brackets
        # ⟦…⟧ (U+27E6 / U+27E7) fall into <unk> for most SentencePiece vocabs
        # and pass through encoder→decoder unchanged.
        placeholder = f"⟦{placeholder_id}⟧"
        regex_pattern = rf"⟦\s*{placeholder_id}\s*⟧"
        return placeholder, regex_pattern

    def validate_configuration(self) -> tuple[bool, str]:
        if (self.lang_in, self.lang_out) not in _SUPPORTED_PAIRS:
            return (
                False,
                f"Argos supports only en→vi in this build "
                f"(got {self.lang_in}→{self.lang_out})",
            )

        try:
            import argostranslate.translate
        except ImportError as e:
            return False, f"argostranslate not installed: {e}"

        if _en_vi_ready:
            return True, "Argos en→vi installed and ready"

        # Pack may already be on disk from a previous run.
        try:
            installed = argostranslate.translate.get_installed_languages()
            from_lang = next((l for l in installed if l.code == "en"), None)
            to_lang = next((l for l in installed if l.code == "vi"), None)
            if from_lang and to_lang and from_lang.get_translation(to_lang):
                return True, "Argos en→vi installed"
        except Exception:
            pass

        return True, "Argos en→vi will download on first use (~80 MB)"
