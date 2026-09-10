"""Let `argostranslate.sbd` import when `stanza` isn't installed.

`argostranslate/sbd.py` does an unguarded top-level `import stanza`, and
`argostranslate/translate.py` imports from `sbd` — so `import stanza` (and
through it torch and transformers, ~590 MB) is on the Argos translate path
whether or not stanza is ever used. The sentencizer this project actually runs
is `MiniSBDSentencizer` (onnxruntime), pinned in
`argos_translator._configure_argos_settings`; `stanza.Pipeline` is only ever
touched inside `StanzaSentencizer.lazy_pipeline`, which is never constructed.

So the shipped sidecar excludes stanza/torch/transformers entirely
(`pdfusion-sidecar.spec`) and satisfies that import with an empty module. Dev
environments still have the real stanza — argostranslate hard-requires it — and
there `install_stanza_stub()` does nothing.

Kept free of heavy imports: it runs before argostranslate is loaded.
"""

import importlib.util
import logging
import sys
import types

logger = logging.getLogger(__name__)

_STUB_ATTR = "__pdfusion_stub__"


def install_stanza_stub() -> bool:
    """Register an empty `stanza` module if the real one is unavailable.

    Returns True if this call (or an earlier one) put the stub in place.
    Idempotent and safe to call from any thread.
    """
    existing = sys.modules.get("stanza")
    if existing is not None:
        return getattr(existing, _STUB_ATTR, False)

    try:
        if importlib.util.find_spec("stanza") is not None:
            return False
    except (ImportError, ValueError):
        pass

    stub = types.ModuleType("stanza")
    stub.__doc__ = "PDFusion stub — see translators/_sbd_compat.py"
    setattr(stub, _STUB_ATTR, True)
    sys.modules["stanza"] = stub
    logger.info("stanza not installed — registered stub for argostranslate.sbd")
    return True
