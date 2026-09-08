"""Process-wide DocLayoutModel cache (issue #23).

Fakes `babeldoc.docvision.doclayout` via `sys.modules` injection so this runs
without babeldoc installed and without importing `processors/processor.py`
(which drags in babeldoc + torch — same trade-off as
test_translation_failure_reporting.py).
"""

from __future__ import annotations

import sys
import threading
import types

import pytest

from desktop_pdf_translator.processors import doc_layout_cache


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    monkeypatch.setattr(doc_layout_cache, "_doc_layout_model", None)


def _fake_babeldoc_doclayout(monkeypatch, load_available):
    fake_module = types.ModuleType("babeldoc.docvision.doclayout")
    fake_module.DocLayoutModel = type(
        "DocLayoutModel", (), {"load_available": staticmethod(load_available)}
    )
    monkeypatch.setitem(sys.modules, "babeldoc.docvision.doclayout", fake_module)


def test_loads_once_and_reuses(monkeypatch):
    calls = []
    sentinel = object()
    _fake_babeldoc_doclayout(monkeypatch, lambda: calls.append(1) or sentinel)

    first = doc_layout_cache.get_shared_doc_layout_model()
    second = doc_layout_cache.get_shared_doc_layout_model()

    assert first is sentinel
    assert second is sentinel
    assert len(calls) == 1


def test_concurrent_first_calls_load_exactly_once(monkeypatch):
    calls = []
    load_started = threading.Event()
    release_load = threading.Event()

    def fake_load_available():
        calls.append(1)
        load_started.set()
        release_load.wait(timeout=2)
        return object()

    _fake_babeldoc_doclayout(monkeypatch, fake_load_available)

    results = []
    threads = [
        threading.Thread(target=lambda: results.append(doc_layout_cache.get_shared_doc_layout_model()))
        for _ in range(5)
    ]
    for t in threads:
        t.start()
    assert load_started.wait(timeout=2)
    release_load.set()
    for t in threads:
        t.join(timeout=2)

    assert len(calls) == 1
    assert len({id(r) for r in results}) == 1
