"""Process-wide cache for BabelDOC's DocLayout-YOLO model.

Kept free of babeldoc/torch imports at module scope, same rule as the rest of
`processors/` — this makes the cache cheap to import and unit-test on its own.
"""

import logging
import threading

logger = logging.getLogger(__name__)

_doc_layout_model = None
_lock = threading.Lock()


def get_shared_doc_layout_model():
    """Process-wide DocLayoutModel, loaded once. Babeldoc has no cache of its
    own — every uncached `TranslationConfig(doc_layout_model=None)` rebuilds
    the ONNX session from scratch (~3-4s). Blocking; call via
    `asyncio.to_thread`.

    Safe to share across concurrently-running chunks/jobs once loaded:
    `OnnxModel.handle_document` only locks its own PyMuPDF rasterization
    step, not `InferenceSession.run()`, which onnxruntime supports calling
    concurrently on one session.
    """
    global _doc_layout_model
    if _doc_layout_model is None:
        with _lock:
            if _doc_layout_model is None:
                from babeldoc.docvision.doclayout import DocLayoutModel
                logger.info("Loading DocLayout-YOLO model (first use this process)")
                _doc_layout_model = DocLayoutModel.load_available()
    return _doc_layout_model
