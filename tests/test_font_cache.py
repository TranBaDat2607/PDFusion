"""BabelDOC loads every embedding font again for every `FontMapper` it builds.

A mapper reads each font of the target language from disk into a
`pymupdf.Font` (34 files, ~266 MB for Vietnamese), and BabelDOC builds six or
seven mappers per translated page, one per pipeline stage. A book is translated
page by page, so a run loaded hundreds of GB of fonts and the sidecar ran out of
memory. `font_cache.install()` makes every mapper in the process share one
loaded font per file. The risky half is sharing more than the fonts: a mapper
that also shared its config or containers would couple stages that must stay
independent, and a memo wrapped around a shared font's methods once per mapper
makes every lookup pay for all the layers before it.

These tests pin that the fonts really are shared (by every stage that builds a
mapper, so the hook has to be the one they all use; across languages; between
threads that start together; for as long as the process lives) and that nothing
else is: a mapper keeps its own config and containers, and a shared font is
still the font BabelDOC's own constructor would have built. A load that failed
leaves nothing behind. The hook goes only onto a BabelDOC whose constructor the
module's copy was checked against, since a constructor it doesn't match could
break every stage while an unpatched one only costs memory. And the translate
job path installs it.

`fontmap.assets` is stubbed with real but tiny fonts (PyMuPDF's built-in ones,
written under `tmp_path`), so nothing downloads and the suite doesn't load 266
MB. Expected values come from that stub's data, or from a mapper built by
BabelDOC's own constructor, never from the code under test.
"""

from __future__ import annotations

import gc
import importlib
import inspect
import logging
import sys
import threading
import time
import types
import uuid
import weakref

import pymupdf
import pytest

from desktop_pdf_translator.processors import font_cache, font_verify_cache

babeldoc = pytest.importorskip("babeldoc")
fontmap = pytest.importorskip("babeldoc.format.pdf.document_il.utils.fontmap")

_LOGGER = "desktop_pdf_translator.processors.font_cache"

# Captured now: a test may swap `pymupdf.Font` for a slower subclass, and the
# helpers below must still recognise every font however it was built.
_FONT = pymupdf.Font

# In none of the stub fonts (they are Latin), so lookups that must come up
# empty are exercised too.
_CJK = chr(0x4E2D)

# Named like BabelDOC's own fonts: `FontMapper.map_in_type` tells serif from sans
# by looking for "serif" in the font id, because the fonts' own flags say serif
# for all of them.
_BUILTIN_FONT = {
    "TestSerif-Regular": "tiro",
    "TestSerif-Bold": "tibo",
    "TestSans-Regular": "helv",
    "TestSans-Bold": "hebo",
    "TestSans-Italic": "heit",
    "TestFallback-Regular": "cour",
    # In the store, but in no family the stub hands out unless a test says so.
    "TestExtra-Regular": "cobo",
}

# Stages that take nothing but a config and build a mapper from it.
_STAGES = (
    ("styles_and_formulas", "StylesAndFormulas"),
    ("paragraph_finder", "ParagraphFinder"),
    ("typesetting", "Typesetting"),
)

# Containers the constructor builds fresh. The `*_font_ids` lists are left out:
# BabelDOC aliases them to the lists in the font family it is handed.
_OWN_CONTAINERS = (
    "font_file_names",
    "fonts",
    "fontid2fontpath",
    "fontid2font",
    "normal_fonts",
    "script_fonts",
    "fallback_fonts",
    "type2font",
)

_MEMOIZED_METHODS = ("has_glyph", "char_lengths")


@pytest.fixture
def original_init(monkeypatch):
    """BabelDOC's own constructor, with `fontmap` put back after the test
    whatever `install()` did to it.

    Unwrapped first: if anything earlier in the run installed the hook for real,
    the wrapper it left must not be mistaken for BabelDOC's own. The class
    binding is restored too, so a hook that rebinds the name instead of patching
    the class can't leak into the next test's stage imports and pass there.
    """
    original = inspect.unwrap(fontmap.FontMapper.__init__)
    monkeypatch.setattr(fontmap.FontMapper, "__init__", original)
    monkeypatch.setattr(fontmap, "FontMapper", fontmap.FontMapper)
    return original


@pytest.fixture
def stub_fonts(monkeypatch, tmp_path, original_init):
    """Seven real font files behind `fontmap.assets`, as a mapper asks for them.

    Six make up the family the stub hands out for any language; `extra` is in
    the store but in no family. Every test gets font names of its own: the
    shared cache outlives a test, and nothing it remembers from another one
    should be able to answer this one.
    """
    token = uuid.uuid4().hex[:8]

    def named(stem):
        return f"{stem}-{token}.ttf"

    paths = {}
    for stem, builtin in _BUILTIN_FONT.items():
        path = tmp_path / named(stem)
        path.write_bytes(pymupdf.Font(builtin).buffer)
        paths[path.name] = path
    # A different value per font, so a font handed another font's metadata shows.
    metadata = {
        name: {"ascent": 900 + 7 * i, "descent": -(250 + 3 * i), "encoding_length": 1 + i % 2}
        for i, name in enumerate(paths)
    }
    family = {
        "normal": [
            named("TestSerif-Regular"),
            named("TestSerif-Bold"),
            named("TestSans-Regular"),
            named("TestSans-Bold"),
        ],
        "script": [named("TestSans-Italic")],
        "fallback": [named("TestFallback-Regular")],
        # Listed under "normal" as well, like BabelDOC's own base font: its
        # constructor loads a name once however many lists carry it.
        "base": [named("TestSans-Regular")],
    }
    names = list(dict.fromkeys(n for kind in ("normal", "script", "fallback", "base") for n in family[kind]))
    stub = types.SimpleNamespace(
        get_font_family=lambda lang_code: family,
        get_font_and_metadata=lambda name: (paths[name], metadata[name]),
    )

    def not_through_the_global(*args, **kwargs):
        raise AssertionError("fonts must come through fontmap.assets")

    # A hook that reached for the real asset store would otherwise start on the
    # 266 MB of real fonts, or the network; fail here instead.
    real_assets = fontmap.assets
    monkeypatch.setattr(real_assets, "get_font_family", not_through_the_global)
    monkeypatch.setattr(real_assets, "get_font_and_metadata", not_through_the_global)
    monkeypatch.setattr(fontmap, "assets", stub)
    return types.SimpleNamespace(
        names=names, paths=paths, metadata=metadata, extra=named("TestExtra-Regular")
    )


@pytest.fixture
def eager_thread_switching():
    """Switch threads between almost any two bytecodes, so a missing lock shows.

    The interval is process-wide, hence put back whatever the test does.
    """
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(previous)


def _config(**fields):
    """What `FontMapper.__init__` reads from a `TranslationConfig`, and keeps."""
    return types.SimpleNamespace(**{"lang_out": "vi", "primary_font_family": None, **fields})


def _restub(monkeypatch, **replacements):
    """Swap parts of the stubbed `fontmap.assets` for a test's own; the rest answers as before."""
    current = fontmap.assets
    parts = {
        "get_font_family": current.get_font_family,
        "get_font_and_metadata": current.get_font_and_metadata,
        **replacements,
    }
    monkeypatch.setattr(fontmap, "assets", types.SimpleNamespace(**parts))


def _assert_same_font_attributes(font, reference, name):
    """`font` carries what BabelDOC's constructor left on `reference`.

    Compared as instance attributes, so one BabelDOC starts setting on a font
    later is held to as well. PyMuPDF's own handle (`this`) differs by
    construction. A memo is compared by being one, with the same settings,
    around the same method: what it wraps is a different font's bound method.
    """
    own = {key: value for key, value in vars(font).items() if key != "this"}
    expected = {key: value for key, value in vars(reference).items() if key != "this"}
    assert set(own) == set(expected), f"{name}: attributes differ"
    for key, value in expected.items():
        if hasattr(value, "cache_parameters"):
            assert hasattr(own[key], "cache_parameters"), f"{name}.{key} is not memoized"
            assert own[key].cache_parameters() == value.cache_parameters(), f"{name}.{key}"
            assert own[key].__wrapped__.__name__ == value.__wrapped__.__name__, f"{name}.{key}"
        else:
            assert own[key] == value, f"{name}.{key}"


def _count_font_loads(monkeypatch):
    """Record every font file read into a `pymupdf.Font`, as `{file: times}`.

    Identity of what mappers hold says nothing about how often it was built: a
    cache that loads first and keeps whichever copy arrived first hands out one
    object while every mapper pays for 266 MB and leaves its copy to the GC.
    """
    loads: dict[str, int] = {}

    class CountingFont(_FONT):
        def __init__(self, *args, **kwargs):
            key = str(kwargs.get("fontfile", args[0] if args else None))
            loads[key] = loads.get(key, 0) + 1
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(pymupdf, "Font", CountingFont)
    return loads


def _slow_font_loads(monkeypatch, seconds):
    """Make loading a font take a while, as a real font's megabytes do.

    The stub's fonts load in microseconds, which hides the window between a
    cache looking for a font and storing it. Sleeping releases the GIL, so
    every thread gets into that window however the platform schedules them.
    Only bites where fonts are built through `pymupdf.Font` looked up at call
    time; elsewhere the test falls back on its other aids.
    """

    class SlowFont(_FONT):
        def __init__(self, *args, **kwargs):
            time.sleep(seconds)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(pymupdf, "Font", SlowFont)


def _fonts_in(value):
    """Every `pymupdf.Font` reachable through dicts, lists and tuples."""
    if isinstance(value, _FONT):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _fonts_in(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _fonts_in(item)


def _shape(mapper):
    """What a mapper holds, each font reduced to its id.

    Mappers that loaded their fonts separately can then be compared. The two
    per-instance memos are callables; only their presence is compared.
    """

    def reduced(value):
        if isinstance(value, _FONT):
            return ("font", getattr(value, "font_id", None))
        if isinstance(value, dict):
            return {key: reduced(item) for key, item in value.items()}
        if isinstance(value, list):
            return [reduced(item) for item in value]
        if callable(value):
            return "<callable>"
        return value

    return {name: reduced(value) for name, value in vars(mapper).items()}


def _font_id(font):
    return None if font is None else font.font_id


class _Rendezvous:
    """Holds the threads that ask for one font until all of them have, or a
    moment has passed.

    That lines them up at the point where a cache decides whether it has the
    font. Not a `threading.Barrier`: an implementation that keeps its lock across
    the lookup would deadlock on it, the thread holding the lock waiting for
    peers that are waiting for the lock. A timeout that opens the gate for good
    costs the race its edge, never a false failure.
    """

    def __init__(self, parties, timeout):
        self._parties = parties
        self._timeout = timeout
        self._lock = threading.Lock()
        self._gates = {}

    def __call__(self, key):
        with self._lock:
            state = self._gates.setdefault(key, [0, threading.Event()])
            state[0] += 1
            if state[0] >= self._parties:
                state[1].set()
            gate = state[1]
        if not gate.wait(self._timeout):
            gate.set()


# ---------------------------------------------------------------------------
# The fonts are shared
# ---------------------------------------------------------------------------


def test_two_mappers_for_one_language_share_every_font(stub_fonts):
    font_cache.install()

    a, b, c = (fontmap.FontMapper(_config()) for _ in range(3))

    assert set(a.fonts) == set(stub_fonts.names)
    for name in stub_fonts.names:
        assert a.fonts[name] is b.fonts[name], f"{name} was loaded again for the next mapper"
    # However a mapper hands a font out (`fonts`, `fontid2font`, the per-type
    # lists) it is the one object: a copy on any route still fills memory.
    loaded = {id(font) for mapper in (a, b, c) for font in _fonts_in(vars(mapper))}
    assert len(loaded) == len(stub_fonts.names)


def test_the_stages_that_build_mappers_get_the_shared_fonts(stub_fonts):
    # Imported before install(): a stage binds `FontMapper` when its module is
    # imported, so a hook that swapped the class inside `fontmap` would never
    # reach it.
    stages = [
        getattr(importlib.import_module(f"babeldoc.format.pdf.document_il.midend.{module}"), name)
        for module, name in _STAGES
    ]
    font_cache.install()
    config = _config()

    baseline = fontmap.FontMapper(config)
    for stage in stages:
        mapper = stage(config).font_mapper
        assert isinstance(mapper, fontmap.FontMapper)
        for name in stub_fonts.names:
            assert mapper.fonts[name] is baseline.fonts[name], (
                f"{stage.__name__} loaded {name} for itself"
            )


# ---------------------------------------------------------------------------
# Nothing else is
# ---------------------------------------------------------------------------


def test_each_mapper_keeps_the_config_it_was_built_with(stub_fonts):
    serif_config = _config(primary_font_family="serif")
    sans_config = _config(primary_font_family="sans-serif")
    font_cache.install()

    a = fontmap.FontMapper(serif_config)
    b = fontmap.FontMapper(sans_config)

    assert a.translation_config is serif_config
    assert b.translation_config is sans_config
    # Derived from the config in the constructor, so a mapper rebuilt from a
    # remembered one would carry the first config's choice.
    assert a.primary_font_family == fontmap.PrimaryFontFamily.SERIF
    assert b.primary_font_family == fontmap.PrimaryFontFamily.SANS_SERIF


def test_mappers_share_fonts_but_own_everything_else(stub_fonts):
    font_cache.install()
    a = fontmap.FontMapper(_config())
    b = fontmap.FontMapper(_config())
    untouched = _shape(a)

    for attr in _OWN_CONTAINERS:
        assert getattr(a, attr) is not getattr(b, attr), f"{attr} is shared between mappers"
    assert a.has_char is not b.has_char
    assert a.map_in_type is not b.map_in_type

    # Emptying every container of one mapper reaches neither a mapper built
    # before it nor one built after: a cache that handed out its own dicts and
    # lists would empty the next mapper's too.
    for attr in _OWN_CONTAINERS:
        getattr(a, attr).clear()
    c = fontmap.FontMapper(_config())
    assert _shape(b) == untouched
    assert _shape(c) == untouched


def test_a_mapper_is_what_babeldocs_own_constructor_builds(stub_fonts, original_init):
    font_cache.install()
    config = _config(primary_font_family="serif")

    built = fontmap.FontMapper(config)
    reference = object.__new__(fontmap.FontMapper)
    original_init(reference, config)

    # A constructor that re-creates BabelDOC's would silently miss an attribute
    # added by the next release, to the mapper or to a font; this is where that
    # shows, and the check to lean on when bumping babeldoc.
    assert _shape(built) == _shape(reference)
    assert set(built.fonts) == set(reference.fonts)
    for name, expected in reference.fonts.items():
        _assert_same_font_attributes(built.fonts[name], expected, name)
    queries = [
        (bold, italic, False, serif, char, font_type)
        for bold in (False, True)
        for italic in (False, True)
        for serif in (False, True)
        for char in ("a", _CJK)
        for font_type in ("normal", "script", "fallback", "base")
    ]
    for query in queries:
        assert _font_id(built.map_in_type(*query)) == _font_id(reference.map_in_type(*query)), query


# ---------------------------------------------------------------------------
# A shared font is the font BabelDOC would have built
# ---------------------------------------------------------------------------


def test_a_shared_font_carries_what_babeldocs_constructor_sets(stub_fonts):
    font_cache.install()
    first = fontmap.FontMapper(_config())
    later = fontmap.FontMapper(_config())

    for name in stub_fonts.names:
        font = later.fonts[name]
        assert font is first.fonts[name], f"{name} is not shared"
        expected = stub_fonts.metadata[name]
        assert font.font_id == name
        assert font.font_path == stub_fonts.paths[name]
        assert font.ascent_fontmap == expected["ascent"]
        assert font.descent_fontmap == expected["descent"]
        assert font.encoding_length == expected["encoding_length"]


def test_later_mappers_do_not_wrap_a_shared_fonts_methods_again(stub_fonts):
    font_cache.install()
    first = fontmap.FontMapper(_config())
    memoized = {
        (name, method): getattr(font, method)
        for name, font in first.fonts.items()
        for method in _MEMOIZED_METHODS
    }

    for _ in range(3):
        later = fontmap.FontMapper(_config())

    for name, font in later.fonts.items():
        assert font is first.fonts[name], f"{name} is not shared"
        for method in _MEMOIZED_METHODS:
            current = getattr(font, method)
            assert current is memoized[name, method], f"{name}.{method} was replaced"
            assert hasattr(current, "cache_info"), f"{name}.{method} is not memoized"
            # `lru_cache` keeps what it wraps as `__wrapped__`: the font's own
            # bound method, unless a second layer was put around the first.
            assert getattr(current.__wrapped__, "__self__", None) is font, (
                f"{name}.{method} is wrapped more than once"
            )
        # Expected values from a font nothing here has touched. Twice, so the
        # second answer comes from the memo.
        oracle = pymupdf.Font(fontfile=str(stub_fonts.paths[name]))
        for _ in range(2):
            for char in ("a", "Z", _CJK):
                assert font.has_glyph(ord(char)) == oracle.has_glyph(ord(char))
            assert font.char_lengths("Hello", fontsize=10) == oracle.char_lengths(
                "Hello", fontsize=10
            )
        for method in _MEMOIZED_METHODS:
            assert getattr(font, method).cache_info().hits > 0, f"{name}.{method} never hit"


# ---------------------------------------------------------------------------
# install() is idempotent
# ---------------------------------------------------------------------------


def test_installing_twice_wraps_the_constructor_once(original_init):
    font_cache.install()
    first = fontmap.FontMapper.__init__
    font_cache.install()

    # Whether the second call stacked a wrapper shows only in what `__wrapped__`
    # leads to: the fonts would be shared either way.
    assert fontmap.FontMapper.__init__ is first
    assert getattr(first, "__wrapped__", None) is original_init


# ---------------------------------------------------------------------------
# One font per file, whoever asks and whenever
# ---------------------------------------------------------------------------


def test_a_font_is_cached_per_file_not_per_language(stub_fonts, monkeypatch):
    # BabelDOC's own families are the same 34 files, arranged differently per
    # language, so a cache keyed by language would load all 266 MB again for each
    # language a session translates into.
    shared = [name for name in stub_fonts.names if "Sans" in name]
    other_family = {
        "normal": [*shared, stub_fonts.extra],
        "script": [],
        "fallback": [],
        "base": [shared[0]],
    }
    default_family = fontmap.assets.get_font_family("vi")
    _restub(
        monkeypatch,
        get_font_family=lambda lang_code: other_family if lang_code == "ja" else default_family,
    )
    font_cache.install()

    vi = fontmap.FontMapper(_config(lang_out="vi"))
    ja = fontmap.FontMapper(_config(lang_out="ja"))

    assert set(vi.fonts) == set(stub_fonts.names)
    assert set(ja.fonts) == {*shared, stub_fonts.extra}
    for name in shared:
        assert vi.fonts[name] is ja.fonts[name], f"{name} was loaded again for the second language"
    loaded = {id(font) for mapper in (vi, ja) for font in _fonts_in(vars(mapper))}
    assert len(loaded) == len({*stub_fonts.names, stub_fonts.extra})


def test_each_font_file_is_loaded_once_however_many_mappers_ask(stub_fonts, monkeypatch):
    # BabelDOC's families overlap, so the second language must not reload the
    # files it shares with the first.
    shared = [name for name in stub_fonts.names if "Sans" in name]
    other_family = {
        "normal": [*shared, stub_fonts.extra],
        "script": [],
        "fallback": [],
        "base": [shared[0]],
    }
    default_family = fontmap.assets.get_font_family("vi")
    _restub(
        monkeypatch,
        get_font_family=lambda lang_code: other_family if lang_code == "ja" else default_family,
    )
    loads = _count_font_loads(monkeypatch)
    font_cache.install()

    for _ in range(3):
        fontmap.FontMapper(_config(lang_out="vi"))
    for _ in range(2):
        fontmap.FontMapper(_config(lang_out="ja"))

    expected = {str(stub_fonts.paths[name]) for name in {*stub_fonts.names, stub_fonts.extra}}
    assert set(loads) == expected
    assert {file: times for file, times in loads.items() if times != 1} == {}


def test_threads_building_mappers_at_once_still_share_one_font_per_file(
    stub_fonts, monkeypatch, eager_thread_switching
):
    # Everything here is there to make a cache without a lock lose: threads that
    # start together, are held at each font until all of them have asked for it,
    # and switch constantly, while a font takes a while to load.
    threads = 8
    rendezvous = _Rendezvous(parties=threads, timeout=0.1)
    look_up = fontmap.assets.get_font_and_metadata

    def look_up_once_all_have_asked(name):
        rendezvous(name)
        return look_up(name)

    _restub(monkeypatch, get_font_and_metadata=look_up_once_all_have_asked)
    _slow_font_loads(monkeypatch, 0.01)
    font_cache.install()
    start = threading.Barrier(threads)
    built = [None] * threads
    failures = []

    def build(index):
        try:
            start.wait(timeout=30)
            built[index] = fontmap.FontMapper(_config())
        except BaseException as error:  # noqa: BLE001 - reported below, not lost in the thread
            failures.append(error)

    workers = [threading.Thread(target=build, args=(i,), daemon=True) for i in range(threads)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=60)

    assert not any(worker.is_alive() for worker in workers), "a thread never finished"
    assert not failures, failures
    for name in stub_fonts.names:
        distinct = {id(mapper.fonts[name]) for mapper in built}
        assert len(distinct) == 1, f"{name} was loaded by {len(distinct)} threads"
    loaded = {id(font) for mapper in built for font in _fonts_in(vars(mapper))}
    assert len(loaded) == len(stub_fonts.names)


def test_a_failed_load_is_not_remembered(stub_fonts, original_init):
    # The third font a mapper loads: two are already cached when it fails.
    broken = stub_fonts.names[2]
    path = stub_fonts.paths[broken]
    real_font = path.read_bytes()
    path.write_bytes(b"this is not a font")
    config = _config()

    with pytest.raises(Exception) as expected:
        original_init(object.__new__(fontmap.FontMapper), config)

    font_cache.install()
    # Twice: a failure left behind as a half-built entry would let the second
    # attempt through.
    for _ in range(2):
        with pytest.raises(Exception) as raised:
            fontmap.FontMapper(config)
        assert type(raised.value) is type(expected.value), raised.value

    path.write_bytes(real_font)
    try:
        a = fontmap.FontMapper(config)
    except Exception as error:  # noqa: BLE001 - the point is what it says about the repair
        pytest.fail(f"{broken} still fails to load after it was repaired: {error!r}")
    b = fontmap.FontMapper(config)

    assert set(a.fonts) == set(stub_fonts.names)
    for name in stub_fonts.names:
        assert a.fonts[name] is b.fonts[name], f"{name} is not shared after the repair"
    assert a.fonts[broken].has_glyph(ord("a"))


def test_shared_fonts_outlive_the_mappers_that_asked_for_them(stub_fonts):
    font_cache.install()
    mappers = [fontmap.FontMapper(_config()) for _ in range(3)]
    watching = {name: weakref.ref(font) for name, font in mappers[0].fonts.items()}
    assert set(watching) == set(stub_fonts.names)

    del mappers
    # A mapper and its fonts sit in reference cycles (a bound method memoized on
    # the object it belongs to), so only a collection frees them; run one, so a
    # cache that kept its fonts weakly really loses them.
    gc.collect()
    again = fontmap.FontMapper(_config())

    for name, ref in watching.items():
        assert ref() is not None, f"{name} was freed with the mappers that held it"
        assert ref() is again.fonts[name], f"{name} was loaded again"
        oracle = pymupdf.Font(fontfile=str(stub_fonts.paths[name]))
        for char in ("a", "Z", _CJK):
            assert again.fonts[name].has_glyph(ord(char)) == oracle.has_glyph(ord(char))


# ---------------------------------------------------------------------------
# Only for a BabelDOC the copy was checked against
# ---------------------------------------------------------------------------


def test_an_unverified_babeldoc_is_left_unpatched_with_a_warning(
    stub_fonts, original_init, monkeypatch, caplog
):
    version = "0.0.0+unverified"
    assert version not in font_cache.VERIFIED_BABELDOC
    monkeypatch.setattr(babeldoc, "__version__", version)

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        font_cache.install()

    assert fontmap.FontMapper.__init__ is original_init
    assert not hasattr(fontmap.FontMapper.__init__, "__wrapped__")
    a = fontmap.FontMapper(_config())
    b = fontmap.FontMapper(_config())
    for name in stub_fonts.names:
        assert a.fonts[name] is not b.fonts[name], f"{name} is shared on an unverified BabelDOC"
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.name == _LOGGER and record.levelno == logging.WARNING
    ]
    assert any(version in message for message in warnings), (
        f"no WARNING naming {version} was logged on {_LOGGER}; got {warnings}"
    )


def test_a_babeldoc_without_a_version_is_treated_as_unverified(
    stub_fonts, original_init, monkeypatch, caplog
):
    monkeypatch.delattr(babeldoc, "__version__")

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        font_cache.install()

    assert fontmap.FontMapper.__init__ is original_init
    assert any(
        record.name == _LOGGER and record.levelno == logging.WARNING for record in caplog.records
    ), "no WARNING was logged on a BabelDOC that reports no version"


def test_the_installed_babeldoc_is_one_the_copy_was_checked_against():
    assert babeldoc.__version__ in font_cache.VERIFIED_BABELDOC, (
        f"BabelDOC {babeldoc.__version__} is not in font_cache.VERIFIED_BABELDOC, so "
        "install() leaves its FontMapper alone and every mapper loads its own fonts "
        "again. Re-check font_cache's copy of FontMapper.__init__ against this "
        "version's babeldoc/format/pdf/document_il/utils/fontmap.py, then add the "
        "version to the set."
    )


# ---------------------------------------------------------------------------
# The job path installs it
# ---------------------------------------------------------------------------


def test_the_job_path_installs_the_shared_fonts(monkeypatch):
    """`_load_engine` is the only place that installs the shared fonts (the boot
    warm thread installs just the hash memo), and every job runs it before
    BabelDOC builds a mapper; without this call each stage would load every
    font again."""
    import desktop_pdf_translator.processors as processors_pkg
    from desktop_pdf_translator.api.routes import translation

    stub = types.ModuleType("processor")
    stub.PDFProcessor = type("PDFProcessor", (), {})
    events = types.ModuleType("events")
    events.EventType = type("EventType", (), {})
    monkeypatch.setattr(processors_pkg, "processor", stub, raising=False)
    monkeypatch.setattr(processors_pkg, "events", events, raising=False)
    monkeypatch.setitem(sys.modules, "desktop_pdf_translator.processors.processor", stub)
    monkeypatch.setitem(sys.modules, "desktop_pdf_translator.processors.events", events)
    calls: list[str] = []
    # Both replaced: this runs the job path for real, and neither install may
    # reach BabelDOC from here.
    monkeypatch.setattr(font_verify_cache, "install", lambda: calls.append("hash memo"))
    monkeypatch.setattr(font_cache, "install", lambda: calls.append("fonts"))

    translation._load_engine()

    assert calls.count("fonts") == 1, calls
