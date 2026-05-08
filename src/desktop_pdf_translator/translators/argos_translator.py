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

from .base import BaseTranslator


logger = logging.getLogger(__name__)


_SUPPORTED_PAIRS = {("en", "vi")}

# Concurrent paragraph workers must not all attempt to download the pack.
_install_lock = threading.Lock()
_en_vi_ready = False

# In-memory translation cache shared across paragraph workers AND across
# translate jobs in the same sidecar lifetime. Argos is deterministic, so a
# repeat of the same source paragraph (e.g. user clicks Translate twice on the
# same PDF) returns instantly without invoking the model. Capped to keep
# memory bounded; entries beyond the cap are evicted FIFO.
_CACHE_MAX_ENTRIES = 20_000
_cache_lock = threading.Lock()
_cache: dict[tuple[str, str, str], str] = {}


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
            "ArgosTranslator configured: %s -> %s", self.lang_in, self.lang_out
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

        cache_key = ("en", "vi", processed)
        with _cache_lock:
            cached = _cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            _ensure_en_vi_installed()
            import argostranslate.translate

            translated = argostranslate.translate.translate(processed, "en", "vi")
            result = self._postprocess_text(translated)
            with _cache_lock:
                if len(_cache) >= _CACHE_MAX_ENTRIES:
                    # Evict oldest entry (Python dicts preserve insertion order)
                    _cache.pop(next(iter(_cache)), None)
                _cache[cache_key] = result
            return result
        except ValueError:
            raise
        except Exception as e:
            return self._handle_translation_error(e, text)

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
