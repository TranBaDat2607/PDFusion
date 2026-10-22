"""Hand freed heap back to the OS between translated pages.

A 346-page translation runs every page through BabelDOC inside the one
long-lived sidecar, two at a time. On Linux it climbed to 7 GB and ran out of
memory, although Python had freed each page: forcing `malloc_trim` brought RSS
back to ~780 MB every time. glibc keeps what it frees inside its arenas and
gives it back on its own only from the very top of the heap, so the page
buffers' freed space piled up page after page. `MALLOC_ARENA_MAX=2` did not
help — the arenas are not too many, they are fragmented — and `malloc_trim`,
which also releases free pages from the middle of a heap, does.

Elsewhere this is a no-op. Windows' heap gives large blocks back as they are
freed; whether it holds anything else over a long run is measured separately,
not assumed. musl has no `malloc_trim`. Stdlib-only, so free to import.
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
    trim(0)
    return True
