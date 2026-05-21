"""Argos Translate (offline NMT) backend — free, no API key.

Used as the default backend when no LLM key is configured. Supports
English → Vietnamese only in this MVP; other source languages raise
ValueError directing the user to switch source language or use an LLM.

The argostranslate package is imported lazily inside the methods that need
it, so sidecar startup time stays unaffected for users on LLM and the
~80 MB language pack downloads only on first translate.
"""

import logging
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from .base import BaseTranslator
from .translation_cache import llm_cache_get as _llm_cache_get, llm_cache_set as _llm_cache_set


logger = logging.getLogger(__name__)


_SUPPORTED_PAIRS = {("en", "vi")}

# Concurrent paragraph workers must not all attempt to download the pack.
_install_lock = threading.Lock()
_en_vi_ready = False

# Argos/CTranslate2 settings are mutated module-globals. Configure them once,
# before the first ctranslate2.Translator is constructed (which happens lazily
# inside PackageTranslation.hypotheses on first translate). `beam_size` is read
# per-call so it stays effective; `device`/`compute_type`/`inter_threads` are
# baked into the Translator at construction and cannot change afterwards.
_settings_lock = threading.Lock()
_argos_configured = False

# (processed_text, original_text, event, result_slot)
_BatchEntry = tuple[str, str, threading.Event, list]


def _preview(s: str, n: int = 60) -> str:
    """Single-line truncated preview for log lines."""
    s = (s or "").replace("\n", " ").replace("\r", " ").strip()
    return (s[:n] + "…") if len(s) > n else s


def _detect_device() -> str:
    """Probe for a usable CUDA device; fall back to CPU silently.

    Argos uses CTranslate2 under the hood; CTranslate2 reports CUDA capability
    independently of the Python ML stack. If the wheel installed in this env
    is CPU-only, `get_cuda_device_count()` returns 0 (or the import raises) —
    either way we land on CPU.
    """
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception as exc:  # noqa: BLE001
        logger.debug("CUDA probe failed (%s); falling back to CPU", exc)
    return "cpu"


def _detect_compute_type(device: str) -> str:
    """Best speed/accuracy compute type per device. int8 on CPU is ~1.5-2x
    over float32; int8_float16 on CUDA combines tensor-core fp16 matmul with
    int8 weight storage."""
    return "int8_float16" if device == "cuda" else "int8"


def _configure_argos_settings() -> None:
    """One-time tuning of `argostranslate.settings` for max throughput.

    Must run before the first ctranslate2.Translator is constructed. Idempotent
    and thread-safe — called from `_ensure_en_vi_installed`, which gates every
    translate() entry into the batching path.
    """
    global _argos_configured
    if _argos_configured:
        return
    with _settings_lock:
        if _argos_configured:
            return
        import argostranslate.settings as _argos_settings

        device = _detect_device()
        compute_type = _detect_compute_type(device)
        _argos_settings.beam_size = 1                # greedy decoding
        _argos_settings.device = device
        _argos_settings.compute_type = compute_type
        # Pipeline overlap: while batch N is decoding, the encoder can start
        # batch N+1. Only worth it on >=4 cores; below that the context-switch
        # cost outweighs the win.
        if (os.cpu_count() or 1) >= 4:
            _argos_settings.inter_threads = 2

        logger.info(
            "Argos CTranslate2 tuned: device=%s, compute_type=%s, "
            "beam_size=%d, inter_threads=%d",
            _argos_settings.device,
            _argos_settings.compute_type,
            _argos_settings.beam_size,
            _argos_settings.inter_threads,
        )
        _argos_configured = True


def _find_bundled_pack() -> Optional[Path]:
    """Locate the pre-bundled Argos en→vi .argosmodel, if shipped with the app.

    Search order:
      1. PyInstaller-frozen exe: `<_MEIPASS>/argos_pack/translate-en_vi.argosmodel`
         — populated by pdfusion-sidecar.spec when assets/argos/ has the file.
      2. Repo dev mode: `<repo>/assets/argos/translate-en_vi.argosmodel` —
         present if the developer copied it in from
         `~/.local/cache/argos-translate/downloads/`.
    Returns the path if the file exists, else None (caller falls back to
    the network-download install path).
    """
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "argos_pack" / "translate-en_vi.argosmodel")
    # Walk up from this file (src/desktop_pdf_translator/translators/argos_translator.py)
    # to the repo root four levels up: file → translators → desktop_pdf_translator
    # → src → repo. The `parents[3]` index reflects that.
    try:
        repo_root = Path(__file__).resolve().parents[3]
        candidates.append(repo_root / "assets" / "argos" / "translate-en_vi.argosmodel")
    except IndexError:
        pass
    for c in candidates:
        if c.is_file():
            return c
    return None


def _ensure_en_vi_installed() -> None:
    """Install the en→vi Argos package if not already present.

    Idempotent and thread-safe. Network access only on first call when no
    pack is on disk AND no bundled copy is shipped with the app.
    """
    # Tune CTranslate2 *before* any Translator is built. Cheap if already done.
    _configure_argos_settings()

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

        # Offline-first install: if the app shipped the .argosmodel pack,
        # install from disk instead of touching the network. This is the
        # production path for the bundled MSI; the network path is a
        # fallback for dev or for installers built without the pack.
        bundled = _find_bundled_pack()
        if bundled is not None:
            logger.info("Argos en→vi pack: installing from bundled file %s", bundled)
            argostranslate.package.install_from_path(str(bundled))
            logger.info("Argos en→vi pack installed (offline, from bundle)")
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
        logger.info("Argos en→vi pack installed (downloaded)")
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
        # to `pool_max_workers` threads concurrently across up to
        # `max_parallel_chunks` BabelDOC sub-jobs (≈16 paragraphs in flight at
        # peak with the qps throttle disabled — see processor.py). We coalesce
        # them and feed the whole batch into one CTranslate2 translate_batch
        # call so the underlying engine packs sentences across paragraphs.
        # `batch_size=16` matches that peak; `batch_timeout=0.4s` keeps the
        # tail of each chunk snappy without losing batching benefit.
        self.batch_size = max(1, int(kwargs.get("batch_size", 16)))
        self.batch_timeout = float(kwargs.get("batch_timeout", 0.4))
        self._batch_lock = threading.Lock()
        self._pending: list[_BatchEntry] = []
        self._flush_timer: threading.Timer | None = None
        self._batch_counter = 0

        # Lazy-resolved CTranslate2 handles. Populated on first batch via
        # `_resolve_native_handles()` so we can call translate_batch directly
        # across all paragraphs in a batch instead of looping through Argos's
        # per-paragraph high-level translate() indirection.
        self._native_lock = threading.Lock()
        self._ct2_translator = None
        self._tokenizer = None
        self._sentencizer = None
        self._target_prefix: str = ""

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

    def _resolve_native_handles(self) -> None:
        """Lazily grab CTranslate2 + tokenizer + sentencizer handles from the
        installed Argos package so we can bypass Argos's per-paragraph
        high-level translate() and call ct2.translate_batch directly across
        all paragraphs in our batch. Idempotent; thread-safe."""
        if self._ct2_translator is not None:
            return
        with self._native_lock:
            if self._ct2_translator is not None:
                return
            import argostranslate.translate as _argos_translate

            installed = _argos_translate.get_installed_languages()
            from_lang = next((l for l in installed if l.code == "en"), None)
            to_lang = next((l for l in installed if l.code == "vi"), None)
            if from_lang is None or to_lang is None:
                raise RuntimeError(
                    "Argos en/vi languages not installed — "
                    "_ensure_en_vi_installed should have run first"
                )
            translation = from_lang.get_translation(to_lang)
            if translation is None:
                raise RuntimeError("Argos en→vi translation not available")

            # `get_translation` returns a CachedTranslation that wraps a
            # PackageTranslation (sometimes through more layers). The
            # ct2 Translator, tokenizer, sentencizer, and target_prefix
            # live on the PackageTranslation at the bottom — unwrap until
            # we find them. Cap the depth so a future Argos refactor that
            # introduces a cycle can't spin forever.
            pkg_translation = translation
            for _ in range(8):
                if hasattr(pkg_translation, "pkg") and hasattr(pkg_translation, "sentencizer"):
                    break
                inner = getattr(pkg_translation, "underlying", None)
                if inner is None or inner is pkg_translation:
                    break
                pkg_translation = inner
            if not (hasattr(pkg_translation, "pkg")
                    and hasattr(pkg_translation, "sentencizer")):
                raise RuntimeError(
                    "Could not locate PackageTranslation under "
                    f"{type(translation).__name__}"
                )

            # Force lazy ctranslate2.Translator creation under the settings
            # we configured in _configure_argos_settings(). Call hypotheses
            # on the *unwrapped* PackageTranslation so the .translator
            # attribute lands there (not on the cache wrapper).
            pkg_translation.hypotheses("warmup.", num_hypotheses=1)
            self._ct2_translator = pkg_translation.translator
            self._tokenizer = pkg_translation.pkg.tokenizer
            self._sentencizer = pkg_translation.sentencizer
            self._target_prefix = pkg_translation.pkg.target_prefix or ""
            logger.info(
                "Argos native handles resolved (target_prefix=%r)",
                self._target_prefix,
            )

    def _translate_batch_native(
        self, batch: list[_BatchEntry], batch_id: int
    ) -> None:
        """True cross-paragraph batching: one CTranslate2 translate_batch call
        covers every sentence from every paragraph in `batch`. Falls through
        to the legacy per-entry path if the native call fails."""
        self._resolve_native_handles()

        # Per-entry plan: split into sentences, tokenize each sentence, record
        # how many sentences belong to this entry so we can regroup the
        # flat result list.
        spans: list[tuple[int, int, str, str, threading.Event, list]] = []
        flat_tokens: list[list[str]] = []
        offset = 0
        for processed, original, event, slot in batch:
            sentences = self._sentencizer.split_sentences(processed)
            toks = [self._tokenizer.encode(s) for s in sentences]
            spans.append((offset, len(toks), processed, original, event, slot))
            flat_tokens.extend(toks)
            offset += len(toks)

        if not flat_tokens:
            # Edge case: empty/whitespace-only paragraphs slipped past the
            # caller's strip check. Just close their events with originals.
            for _, _, processed, original, event, slot in spans:
                slot[0] = original
                event.set()
            return

        prefix = (
            [[self._target_prefix]] * len(flat_tokens)
            if self._target_prefix
            else None
        )
        # `beam_size=1` and `num_hypotheses=1` mirror our global settings —
        # explicit here to insulate against future Argos default changes.
        results = self._ct2_translator.translate_batch(
            flat_tokens,
            target_prefix=prefix,
            replace_unknowns=True,
            max_batch_size=64,
            batch_type="tokens",
            beam_size=1,
            num_hypotheses=1,
            length_penalty=0.2,
            return_scores=False,
        )

        for idx, (off, n, processed, original, event, slot) in enumerate(spans):
            try:
                pieces: list[str] = []
                for r in results[off : off + n]:
                    v = self._tokenizer.decode(r.hypotheses[0])
                    if self._target_prefix and v.startswith(self._target_prefix):
                        v = v[len(self._target_prefix):]
                    if v and v[0] == " ":
                        v = v[1:]
                    pieces.append(v)
                # Argos's ITranslation.combine_paragraphs uses "\n" between
                # sentences — we mirror that to preserve any in-paragraph
                # line breaks BabelDOC may rely on.
                translated = "\n".join(pieces)
                result = self._postprocess_text(translated)
                _llm_cache_set(processed, result, "en", "vi", "argos", self.model)
                slot[0] = result
                self._fire_paragraph_callback(processed, result)
                logger.info(
                    "  [%d.%d] EN: %s | VI: %s",
                    batch_id, idx, _preview(processed), _preview(result),
                )
            except Exception as e:  # noqa: BLE001
                slot[0] = self._handle_translation_error(e, original)
            finally:
                event.set()

    def _translate_batch(self, batch: list[_BatchEntry], batch_id: int) -> None:
        """Translate every entry in `batch`, set its event.

        Fast path: one cross-paragraph CTranslate2 call.
        Fallback: per-entry Argos high-level translate() loop (still benefits
        from greedy decoding + int8 settings).
        """
        logger.info(
            "Argos streaming batch #%d: translating %d paragraph(s)",
            batch_id, len(batch),
        )
        try:
            self._translate_batch_native(batch, batch_id)
            return
        except Exception as exc:  # noqa: BLE001
            # If native batching blows up *before* per-entry decode (e.g. the
            # ct2 call itself raised), the per-entry event.set() never ran.
            # Fall through to the slow path so callers don't hang forever.
            logger.warning(
                "Argos native batch #%d failed (%s); falling back to "
                "per-entry path", batch_id, exc,
            )

        import argostranslate.translate

        for idx, (processed, original, event, result_slot) in enumerate(batch):
            if event.is_set():
                # Native path partially succeeded — don't double-translate.
                continue
            try:
                translated = argostranslate.translate.translate(processed, "en", "vi")
                result = self._postprocess_text(translated)
                _llm_cache_set(processed, result, "en", "vi", "argos", self.model)
                result_slot[0] = result
                self._fire_paragraph_callback(processed, result)
                logger.info(
                    "  [%d.%d] EN: %s | VI: %s (fallback)",
                    batch_id, idx, _preview(processed), _preview(result),
                )
            except Exception as e:
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
