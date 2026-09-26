"""Language/service capability rules — the single source of truth for
"which request actually runs, and with what".

Deliberately free of heavyweight imports (no BabelDOC, no torch, no SDK
clients), so the API layer can consult it before accepting a job and the test
suite can exercise it in milliseconds. Importing this module must stay cheap;
put anything that needs a live translator elsewhere.

Two rules used to live in duplicate:

* **default resolution** — "no language given → use the configured default"
  lived only inside `PDFProcessor.process_pdf`, while `api/schemas.py` declared
  non-null defaults of its own that pre-empted it (issue #12);
* **service fallback** — "an LLM with no API key silently becomes Argos" lived
  in `PDFProcessor._resolve_effective_service` *and*, copy-pasted, in the
  `/translate/prewarm` route.

Both now live here.
"""

from __future__ import annotations

import logging
from typing import Dict, FrozenSet, List, Optional, Set, Tuple, Union

from ..config import LanguageCode, TranslationService
from ..providers.registry import PROVIDERS

logger = logging.getLogger(__name__)


LangLike = Union[LanguageCode, str]


# User-facing language names. Lives here rather than in the API layer so the
# capability messages below and the `/config/options` dropdown data can't drift
# apart.
LANGUAGE_LABELS: Dict[LanguageCode, str] = {
    LanguageCode.AUTO: "Auto-detect",
    LanguageCode.VIETNAMESE: "Vietnamese",
    LanguageCode.ENGLISH: "English",
    LanguageCode.JAPANESE: "Japanese",
    LanguageCode.CHINESE_SIMPLIFIED: "Chinese (Simplified)",
    LanguageCode.CHINESE_TRADITIONAL: "Chinese (Traditional)",
}

SERVICE_LABELS: Dict[TranslationService, str] = {
    TranslationService(spec.id): spec.label for spec in PROVIDERS
}


# Which (source, target) pairs each backend can actually produce
# (`ProviderSpec.supported_pairs`):
#
#   None      → no restriction; the backend handles any pair we expose.
#   set[...]  → exhaustive. Anything outside it fails.
SUPPORTED_PAIRS: Dict[TranslationService, Optional[FrozenSet[Tuple[str, str]]]] = {
    TranslationService(spec.id): spec.supported_pairs for spec in PROVIDERS
}

# Backends with no language detection of their own need "auto" pinned to a
# concrete source (`ProviderSpec.auto_source`). LLMs detect from content, so
# they are absent here and "auto" stays "auto" for them.
_AUTO_SOURCE_SUBSTITUTE: Dict[TranslationService, str] = {
    TranslationService(spec.id): spec.auto_source
    for spec in PROVIDERS
    if spec.auto_source
}


def _code(lang: LangLike) -> str:
    """Accept either a `LanguageCode` or a bare wire string."""
    return lang.value if isinstance(lang, LanguageCode) else str(lang)


def normalize_pair(
    service: TranslationService, lang_in: LangLike, lang_out: LangLike
) -> Tuple[str, str]:
    """Resolve a requested pair to the one the backend will really run.

    Only "auto" moves, and only for backends that cannot detect a source
    language. Mirrors `ArgosTranslator._setup_translator`, which performs the
    same substitution on itself.
    """
    source = _code(lang_in)
    target = _code(lang_out)
    if source == "auto":
        source = _AUTO_SOURCE_SUBSTITUTE.get(service, source)
    return source, target


def supported_pairs_for(
    service: TranslationService,
) -> Optional[List[List[str]]]:
    """Wire format for `/config/options`: every pair the frontend may offer,
    or `None` for "unrestricted".

    Auto-source aliases are expanded here rather than re-derived in TypeScript,
    so the substitution rule above stays the only copy.
    """
    pairs = SUPPORTED_PAIRS.get(service)
    if pairs is None:
        return None
    expanded: Set[Tuple[str, str]] = set(pairs)
    substitute = _AUTO_SOURCE_SUBSTITUTE.get(service)
    if substitute is not None:
        expanded |= {
            ("auto", target) for source, target in pairs if source == substitute
        }
    return sorted([source, target] for source, target in expanded)


def unsupported_reason(
    service: TranslationService, lang_in: LangLike, lang_out: LangLike
) -> Optional[str]:
    """Why this request cannot run, or `None` if it can.

    Ask about the *effective* service (see `resolve_effective_service`): with no
    API key, "OpenAI + Japanese" really means "Argos + Japanese".
    """
    pairs = SUPPORTED_PAIRS.get(service)
    if pairs is None:
        return None

    source, target = normalize_pair(service, lang_in, lang_out)
    if (source, target) in pairs:
        return None

    def label(code: str) -> str:
        try:
            return LANGUAGE_LABELS[LanguageCode(code)]
        except ValueError:
            return code

    allowed = ", ".join(
        f"{label(s)} → {label(t)}" for s, t in sorted(pairs)
    )
    return (
        f"{SERVICE_LABELS.get(service, service.value)} translates "
        f"{allowed} only, but {label(source)} → {label(target)} was requested. "
        f"Add an API key in Settings to use a translator that supports it."
    )


def resolve_languages(
    settings,
    source_lang: Optional[LanguageCode],
    target_lang: Optional[LanguageCode],
) -> Tuple[LanguageCode, LanguageCode]:
    """Fill in unspecified languages from configuration.

    `None` is the only value that means "unspecified". Callers must not pass a
    sentinel like `LanguageCode.AUTO` to mean "I didn't choose" — `AUTO` is a
    real, user-selectable source language.
    """
    return (
        source_lang or settings.translation.default_source_lang,
        target_lang or settings.translation.default_target_lang,
    )


def resolve_effective_service(
    settings, requested: TranslationService
) -> TranslationService:
    """Pick the service that will actually run given current credentials.

    Argos (offline) is always usable. For LLM services, fall back to Argos when
    the user has no key configured. Matches the product rule: "Argos is default;
    LLM wins when a key exists".

    Callers deciding whether a request is *supported* must ask about the
    effective service, not the requested one: "OpenAI + Japanese" with no key
    really means "Argos + Japanese", which Argos cannot do.
    """
    if settings.has_api_key(requested):
        return requested
    logger.info("No API key for %s — falling back to Argos for this run", requested)
    return TranslationService.ARGOS
