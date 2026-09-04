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
from typing import Optional, Tuple

from ..config import LanguageCode, TranslationService

logger = logging.getLogger(__name__)


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
