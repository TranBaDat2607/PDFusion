"""Hand freed heap back to the OS between translated pages.

A translation runs every page through BabelDOC inside the one long-lived
sidecar, and glibc keeps what Python frees inside its arenas: it gives memory
back on its own only from the very top of a heap. Measured on a 129-page Argos
run, the sidecar ended ~420 MB above the level `malloc_trim` brought it back to,
and a full `gc.collect()` freed next to nothing — the memory was already free,
just not returned. `malloc_trim` also releases free pages from the middle of a
heap, which is the part that matters here.

Elsewhere this is a no-op. Windows' heap returns large blocks as they are
freed; whether it holds anything else over a long run is measured separately
(#134), not assumed. musl has no `malloc_trim`. Stdlib-only, so free to import.
"""

from __future__ import annotations

import ctypes
import functools
import sys
from typing import Callable, Optional


@functools.cache
def _malloc_trim() -> Optional[Callable[[int], int]]:
    if not sys.platform.startswith("linux"):
        return None
    try:
        libc = ctypes.CDLL("libc.so.6")
    except OSError:
        return None
    return getattr(libc, "malloc_trim", None)


def release_freed_memory() -> bool:
    """Return freed heap pages to the OS; True if glibc was asked to.

    Blocking (it walks every arena), so call it off the event loop.
    """
    trim = _malloc_trim()
    if trim is None:
        return False
    # 0 keeps no slack at the top of the heap; the next page grows it again.
    trim(0)
    return True
