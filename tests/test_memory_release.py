"""Handing freed heap back to the OS between translated pages.

A 346-page run climbed to 7 GB on Linux although Python had freed each page:
glibc kept the pages, and `malloc_trim` brought RSS back to ~780 MB every time.
The helper must trim where glibc is, and be a silent no-op everywhere else.
"""

from __future__ import annotations

import sys

import pytest

from desktop_pdf_translator.utils import memory


@pytest.fixture(autouse=True)
def _fresh_lookup():
    memory._malloc_trim.cache_clear()
    yield
    memory._malloc_trim.cache_clear()


class _FakeLibc:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def malloc_trim(self, pad: int) -> int:
        self.calls.append(pad)
        return 1


def test_off_linux_it_never_loads_a_libc(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("loaded a libc off Linux")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(memory.ctypes, "CDLL", refuse)

    assert memory.release_freed_memory() is False


def test_a_libc_without_malloc_trim_is_a_no_op(monkeypatch):
    # musl has no malloc_trim.
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(memory.ctypes, "CDLL", lambda *_a, **_k: object())

    assert memory.release_freed_memory() is False


def test_no_loadable_libc_is_a_no_op(monkeypatch):
    def missing(*_args, **_kwargs):
        raise OSError("libc.so.6: cannot open shared object file")

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(memory.ctypes, "CDLL", missing)

    assert memory.release_freed_memory() is False


def test_glibc_is_trimmed_to_zero_padding(monkeypatch):
    libc = _FakeLibc()
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(memory.ctypes, "CDLL", lambda *_a, **_k: libc)

    assert memory.release_freed_memory() is True
    assert memory.release_freed_memory() is True
    # 0 keeps no slack at the top of the heap; the next page re-grows it.
    assert libc.calls == [0, 0]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="glibc only")
def test_the_real_glibc_trims():
    assert memory.release_freed_memory() is True
