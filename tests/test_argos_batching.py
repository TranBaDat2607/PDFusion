"""Argos's cross-paragraph batching, against a fake CTranslate2.

`ArgosTranslator.translate()` doesn't translate anything itself: it parks the
caller on a `threading.Event` and hands the paragraph to a batch worker, which
runs one `translate_batch` over the sentences of *every* paragraph in the batch
and then has to hand each result back to the right caller. The regrouping is
index arithmetic over a flat result list, the dispatch is racy by construction
(BabelDOC calls this from its whole worker pool), and every failure mode ends
in "the caller waits forever" or "the wrong paragraph gets the wrong text".

Nothing here imports `argostranslate` or CTranslate2. `_resolve_native_handles`
short-circuits once `_ct2_translator` is set, so the fakes are installed on the
instance and the real resolution never runs — which is also what keeps the
suite off the ~80 MB language pack.
"""

from __future__ import annotations

import threading
import time

import pytest

from desktop_pdf_translator.translators import argos_translator as argos_module
from desktop_pdf_translator.translators.argos_translator import ArgosTranslator


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class _Sentencizer:
    """Splits on an explicit `|` marker.

    A realistic punctuation-based splitter would make every assertion below a
    statement about the splitter instead of about the regrouping under test.
    """

    def split_sentences(self, text: str) -> list[str]:
        return [p for p in text.split("|") if p] or [text]


class _Tokenizer:
    def encode(self, sentence: str) -> list[str]:
        return sentence.split(" ")

    def decode(self, tokens: list[str]) -> str:
        return " ".join(tokens)


class _Hypothesis:
    def __init__(self, tokens: list[str]):
        self.hypotheses = [tokens]


class _CT2:
    """Records every call so the tests can assert on batching itself, not just
    on the text that comes out."""

    def __init__(self, target_prefix: str = "", fail: bool = False):
        self.calls: list[list[list[str]]] = []
        self.target_prefix = target_prefix
        self.fail = fail
        self.delay = 0.0

    def translate_batch(self, tokens, **kwargs):
        self.calls.append([list(t) for t in tokens])
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("ct2 exploded")
        # Upper-casing stands in for translation: it round-trips through
        # `_postprocess_text`'s Vietnamese punctuation rules untouched, so a
        # failed assertion means the batching is wrong, not the fixture.
        prefix = [self.target_prefix] if self.target_prefix else []
        return [_Hypothesis(prefix + [t.upper() for t in sent]) for sent in tokens]


@pytest.fixture(autouse=True)
def _no_pack_install_and_no_cache(monkeypatch: pytest.MonkeyPatch):
    """Skip the ~80 MB pack download and keep the on-disk paragraph cache out
    of it — `llm_cache_get` would otherwise reach the developer's real
    `~/AppData/Local/PDFusion/translation_cache`."""
    monkeypatch.setattr(argos_module, "_ensure_en_vi_installed", lambda: None)
    monkeypatch.setattr(argos_module, "_llm_cache_get", lambda *a, **kw: None)
    monkeypatch.setattr(argos_module, "_llm_cache_set", lambda *a, **kw: None)


def make_translator(ct2: _CT2 | None = None, **kwargs) -> ArgosTranslator:
    ct2 = ct2 or _CT2()
    translator = ArgosTranslator(lang_in="en", lang_out="vi", **kwargs)
    translator._ct2_translator = ct2
    translator._tokenizer = _Tokenizer()
    translator._sentencizer = _Sentencizer()
    translator._target_prefix = ct2.target_prefix
    return translator


@pytest.fixture
def ct2() -> _CT2:
    return _CT2()


@pytest.fixture
def translator(ct2: _CT2):
    t = make_translator(ct2, batch_size=4, batch_timeout=0.05)
    yield t
    t.close()


def translate_concurrently(translator: ArgosTranslator, texts: list[str]) -> list[str]:
    """Call `translate()` from one thread per paragraph, the way BabelDOC's
    worker pool does. Sequential calls would never fill a batch."""
    results: list[str | None] = [None] * len(texts)

    def worker(i: int, text: str) -> None:
        results[i] = translator.translate(text)

    threads = [
        threading.Thread(target=worker, args=(i, t)) for i, t in enumerate(texts)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "a translate() call never returned"
    return results  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# the batching itself
# ---------------------------------------------------------------------------


def test_a_full_batch_is_one_ct2_call(translator: ArgosTranslator, ct2: _CT2):
    """The whole point of the coalescing: four paragraphs from four BabelDOC
    worker threads become one `translate_batch`, so the engine can pack
    sentences across paragraph boundaries."""
    results = translate_concurrently(translator, ["one", "two", "three", "four"])

    assert results == ["ONE", "TWO", "THREE", "FOUR"]
    assert len(ct2.calls) == 1
    assert ct2.calls[0] == [["one"], ["two"], ["three"], ["four"]]


def test_each_caller_gets_its_own_paragraph_back(translator: ArgosTranslator):
    """The results come back as one flat list covering every sentence of every
    paragraph; the regrouping is offset arithmetic, and getting it wrong hands
    paragraph N's text to paragraph N+1's caller."""
    texts = [
        "alpha one|alpha two|alpha three",
        "beta one",
        "gamma one|gamma two",
        "delta one",
    ]
    results = translate_concurrently(translator, texts)

    # Sentences of one paragraph are rejoined with "\n", mirroring Argos's own
    # `combine_paragraphs`, so any in-paragraph line breaks BabelDOC relies on
    # survive.
    assert results[0] == "ALPHA ONE\nALPHA TWO\nALPHA THREE"
    assert results[1] == "BETA ONE"
    assert results[2] == "GAMMA ONE\nGAMMA TWO"
    assert results[3] == "DELTA ONE"


def test_a_partial_batch_is_flushed_by_the_tail_timer(
    translator: ArgosTranslator, ct2: _CT2
):
    """Without the timer the last `< batch_size` paragraphs of a document
    would hang their callers until the 180 s wait timeout."""
    started = time.monotonic()
    assert translator.translate("only one") == "ONLY ONE"
    elapsed = time.monotonic() - started

    assert len(ct2.calls) == 1
    assert elapsed < 5.0


def test_the_timer_is_rearmed_by_each_new_paragraph(
    translator: ArgosTranslator, ct2: _CT2
):
    """Two paragraphs arriving inside one timeout window should ride the same
    batch, not two — otherwise the coalescing degrades to per-paragraph as
    soon as the pool is slower than the timeout."""
    results = translate_concurrently(translator, ["one", "two"])
    assert results == ["ONE", "TWO"]
    assert len(ct2.calls) == 1


def test_more_paragraphs_than_the_batch_size_split_into_several_calls(ct2: _CT2):
    translator = make_translator(ct2, batch_size=2, batch_timeout=0.05)
    try:
        results = translate_concurrently(translator, ["a", "b", "c", "d"])
    finally:
        translator.close()

    assert sorted(results) == ["A", "B", "C", "D"]
    assert len(ct2.calls) == 2
    assert all(len(call) == 2 for call in ct2.calls)


def test_batches_are_numbered_so_the_logs_can_be_read(ct2: _CT2):
    translator = make_translator(ct2, batch_size=1, batch_timeout=0.05)
    try:
        translator.translate("a")
        translator.translate("b")
    finally:
        translator.close()
    assert translator._batch_counter == 2


def test_nothing_is_left_pending_after_a_flush(translator: ArgosTranslator):
    translate_concurrently(translator, ["one", "two", "three", "four"])
    assert translator._pending == []


# ---------------------------------------------------------------------------
# text handling
# ---------------------------------------------------------------------------


def test_the_target_prefix_is_stripped_from_the_output():
    """The prefix is fed to the decoder to steer it and comes back on every
    hypothesis. Leaving it in prefixes every paragraph of the PDF with a
    language tag."""
    ct2 = _CT2(target_prefix="__vi__")
    translator = make_translator(ct2, batch_size=1, batch_timeout=0.05)
    try:
        assert translator.translate("hello") == "HELLO"
    finally:
        translator.close()


def test_the_prefix_is_passed_down_to_the_engine():
    ct2 = _CT2(target_prefix="__vi__")
    translator = make_translator(ct2, batch_size=1, batch_timeout=0.05)
    captured = {}
    real = ct2.translate_batch

    def watched(tokens, **kwargs):
        captured.update(kwargs)
        return real(tokens, **kwargs)

    ct2.translate_batch = watched
    try:
        translator.translate("hello")
    finally:
        translator.close()

    assert captured["target_prefix"] == [["__vi__"]]


def test_no_prefix_means_no_target_prefix_argument():
    ct2 = _CT2(target_prefix="")
    translator = make_translator(ct2, batch_size=1, batch_timeout=0.05)
    captured = {}
    real = ct2.translate_batch

    def watched(tokens, **kwargs):
        captured.update(kwargs)
        return real(tokens, **kwargs)

    ct2.translate_batch = watched
    try:
        translator.translate("hello")
    finally:
        translator.close()

    assert captured["target_prefix"] is None


def test_a_whitespace_only_paragraph_never_reaches_the_engine(
    translator: ArgosTranslator, ct2: _CT2
):
    assert translator.translate("   \n  ") == "   \n  "
    assert ct2.calls == []


def test_an_empty_paragraph_still_counts_as_a_translate_call(
    translator: ArgosTranslator,
):
    """`translate_call_count` is the denominator of the failure banner, so it
    has to cover every unit BabelDOC handed over — including the ones that
    short-circuit."""
    translator.translate("")
    assert translator.translate_call_count == 1


def test_a_cache_hit_skips_the_engine(
    translator: ArgosTranslator, ct2: _CT2, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(argos_module, "_llm_cache_get", lambda *a, **kw: "Đã lưu")
    assert translator.translate("hello") == "Đã lưu"
    assert ct2.calls == []


def test_a_cache_hit_still_fires_the_live_ticker(
    ct2: _CT2, monkeypatch: pytest.MonkeyPatch
):
    """The `paragraph_translated` SSE event drives the overlay's ticker. A
    cached paragraph that fires nothing makes the ticker stall on a re-run,
    which is exactly when every paragraph is cached."""
    monkeypatch.setattr(argos_module, "_llm_cache_get", lambda *a, **kw: "Đã lưu")
    seen: list[tuple[str, str]] = []
    translator = make_translator(
        ct2,
        batch_size=1,
        batch_timeout=0.05,
        on_paragraph_translated=lambda s, t: seen.append((s, t)),
    )
    try:
        translator.translate("hello")
    finally:
        translator.close()

    assert seen == [("hello", "Đã lưu")]


def test_a_translated_paragraph_is_written_to_the_cache(ct2: _CT2, monkeypatch):
    written: list[tuple] = []
    monkeypatch.setattr(
        argos_module, "_llm_cache_set", lambda *a, **kw: written.append(a)
    )
    translator = make_translator(ct2, batch_size=1, batch_timeout=0.05)
    try:
        translator.translate("hello")
    finally:
        translator.close()

    assert written and written[0][:2] == ("hello", "HELLO")


# ---------------------------------------------------------------------------
# failure paths — every one of them has to release the caller
# ---------------------------------------------------------------------------


def test_a_failing_engine_falls_back_to_the_per_entry_path(monkeypatch):
    """If the native call raises before any per-entry `event.set()`, the
    callers are still parked. The fallback loop is what releases them."""
    ct2 = _CT2(fail=True)
    translator = make_translator(ct2, batch_size=2, batch_timeout=0.05)

    class _FakeArgosTranslate:
        @staticmethod
        def translate(text, _src, _dst):
            return f"FALLBACK {text}"

    import sys
    import types

    module = types.ModuleType("argostranslate")
    module.translate = _FakeArgosTranslate()
    monkeypatch.setitem(sys.modules, "argostranslate", module)
    monkeypatch.setitem(sys.modules, "argostranslate.translate", module.translate)

    try:
        results = translate_concurrently(translator, ["one", "two"])
    finally:
        translator.close()

    assert results == ["FALLBACK one", "FALLBACK two"]
    assert translator.failed_translations == 0


def test_a_failure_in_the_fallback_too_returns_source_text_and_is_counted(
    monkeypatch,
):
    """The invariant from "Translator failures are counted, not swallowed": a
    path that hands back source text must go through the funnel, or the run
    reports success and gets cached with the source text in it."""
    ct2 = _CT2(fail=True)
    translator = make_translator(ct2, batch_size=1, batch_timeout=0.05)

    import sys
    import types

    class _Boom:
        @staticmethod
        def translate(*_a, **_kw):
            raise RuntimeError("no pack")

    module = types.ModuleType("argostranslate")
    module.translate = _Boom()
    monkeypatch.setitem(sys.modules, "argostranslate", module)
    monkeypatch.setitem(sys.modules, "argostranslate.translate", module.translate)

    try:
        assert translator.translate("hello") == "hello"
    finally:
        translator.close()

    assert translator.failed_translations == 1


def test_a_failed_pack_install_is_counted_rather_than_silently_untranslated(
    monkeypatch, ct2: _CT2
):
    """`_ensure_en_vi_installed` raises for *every* paragraph when the network
    is down, so an unguarded call meant a first run produced a whole
    untranslated document that looked finished."""

    def no_network():
        raise RuntimeError("could not reach the package index")

    monkeypatch.setattr(argos_module, "_ensure_en_vi_installed", no_network)
    translator = make_translator(ct2, batch_size=1, batch_timeout=0.05)
    try:
        assert translator.translate("hello") == "hello"
    finally:
        translator.close()

    assert translator.failed_translations == 1
    assert ct2.calls == []


def test_a_failure_decoding_one_entry_does_not_take_the_batch_with_it(ct2: _CT2):
    """Per-entry decode is inside its own try/finally so one bad result can't
    leave the other three callers parked."""
    translator = make_translator(ct2, batch_size=2, batch_timeout=0.05)

    class _Exploding(_Tokenizer):
        def decode(self, tokens):
            if "BAD" in tokens:
                raise RuntimeError("decode failed")
            return super().decode(tokens)

    translator._tokenizer = _Exploding()
    try:
        results = translate_concurrently(translator, ["bad", "good"])
    finally:
        translator.close()

    assert results == ["bad", "GOOD"]
    assert translator.failed_translations == 1


def test_a_worker_that_never_fires_the_event_still_releases_the_caller(
    monkeypatch, ct2: _CT2
):
    """The bounded wait exists so a batch worker that dies without hitting its
    `finally: set()` can't wedge a BabelDOC worker thread for the life of the
    job."""
    monkeypatch.setattr(argos_module, "_BATCH_WAIT_TIMEOUT", 0.2)
    translator = make_translator(ct2, batch_size=1, batch_timeout=0.05)
    monkeypatch.setattr(translator, "_translate_batch", lambda *a, **kw: None)

    try:
        assert translator.translate("hello") == "hello"
    finally:
        translator.close()

    assert translator.failed_translations == 1


def test_an_event_fired_without_a_result_is_treated_as_a_failure(
    monkeypatch, ct2: _CT2
):
    """Shouldn't happen — every path assigns the slot — but returning `None`
    from `translate()` would put a literal "None" into the PDF."""
    translator = make_translator(ct2, batch_size=1, batch_timeout=0.05)

    def fire_only(batch, _batch_id):
        for _processed, _original, event, _slot in batch:
            event.set()

    monkeypatch.setattr(translator, "_translate_batch", fire_only)
    try:
        assert translator.translate("hello") == "hello"
    finally:
        translator.close()

    assert translator.failed_translations == 1


# ---------------------------------------------------------------------------
# cancellation and the supported-pair contract
# ---------------------------------------------------------------------------


def test_a_cancelled_job_returns_source_text_without_touching_the_engine(ct2: _CT2):
    cancel = threading.Event()
    cancel.set()
    translator = make_translator(
        ct2, batch_size=1, batch_timeout=0.05, cancel_event=cancel
    )
    try:
        assert translator.translate("hello") == "hello"
    finally:
        translator.close()

    assert ct2.calls == []


def test_a_cancelled_paragraph_is_not_counted_as_a_failure(ct2: _CT2):
    """An intentional stop, not a failure — counting it would make a cancelled
    run look like a partial one."""
    cancel = threading.Event()
    cancel.set()
    translator = make_translator(
        ct2, batch_size=1, batch_timeout=0.05, cancel_event=cancel
    )
    try:
        translator.translate("hello")
    finally:
        translator.close()

    assert translator.failed_translations == 0


def test_the_cancel_check_runs_before_the_cache_lookup(
    ct2: _CT2, monkeypatch: pytest.MonkeyPatch
):
    """A cancelled job shouldn't even pay for a SQLite read."""
    looked_up: list[str] = []
    monkeypatch.setattr(
        argos_module,
        "_llm_cache_get",
        lambda text, *a, **kw: looked_up.append(text) or None,
    )
    cancel = threading.Event()
    cancel.set()
    translator = make_translator(
        ct2, batch_size=1, batch_timeout=0.05, cancel_event=cancel
    )
    try:
        translator.translate("hello")
    finally:
        translator.close()

    assert looked_up == []


def test_auto_source_is_normalized_to_english(ct2: _CT2):
    """Argos has no language detection and `auto` is the app default, so
    without this every Argos run would raise on the pair check."""
    translator = ArgosTranslator(lang_in="auto", lang_out="vi")
    try:
        assert translator.lang_in == "en"
    finally:
        translator.close()


def test_an_unsupported_pair_raises_rather_than_producing_source_text(ct2: _CT2):
    """The pre-flight in `POST /translate` should have caught this; if it ever
    doesn't, failing loudly beats a document that comes out in English."""
    translator = ArgosTranslator(lang_in="ja", lang_out="vi")
    try:
        with pytest.raises(ValueError, match="English"):
            translator.translate("こんにちは")
    finally:
        translator.close()


def test_argos_names_no_rate_limited_service():
    """`_SERVICE_NAME = None` genuinely opts out — Argos is local, and a
    `None`-keyed bucket would be shared with every other backend that never
    named a service."""
    assert ArgosTranslator._SERVICE_NAME is None


def test_close_is_idempotent(translator: ArgosTranslator):
    translator.close()
    translator.close()


# ---------------------------------------------------------------------------
# placeholders
# ---------------------------------------------------------------------------


def test_formula_placeholders_use_brackets_an_nmt_model_passes_through():
    """The default `{v1}` style gets tokenized and split by a SentencePiece
    vocab; the rare bracket pair falls into `<unk>` and survives the round
    trip intact."""
    translator = ArgosTranslator(lang_in="en", lang_out="vi")
    try:
        placeholder, pattern = translator.get_formular_placeholder(7)
    finally:
        translator.close()

    import re

    assert placeholder == "⟦7⟧"
    assert re.search(pattern, "text ⟦ 7 ⟧ more")
