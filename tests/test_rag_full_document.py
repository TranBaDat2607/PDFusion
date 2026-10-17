"""A short document is read whole, whatever the question.

"Summarize this report" or "list every table" need every page, but retrieval
hands the model at most `max_pdf_sources` of them. When the index's text fits
in `full_document_chars`, the answer prompt carries every page; retrieval still
decides which pages are offered as sources and which get an image.

Same fakes as `test_rag_answers.py`: real ChromaDB and PDFs under `tmp_path`,
a hashing embedding function, a recording model.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

import pytest

from desktop_pdf_translator.config import AppSettings
from desktop_pdf_translator.rag import rag_chain as rag_chain_module
from desktop_pdf_translator.rag.vector_store import ChromaDBManager

from desktop_pdf_translator.rag.errors import AnswerGenerationError

from test_rag_answers import (
    _FENCE,
    HashingEmbeddingFunction,
    _SdkError,
    _Translator,
    ask,
    build_document,
    image_labels,
    make_chain,
)


@pytest.fixture
def store(tmp_path: Path) -> ChromaDBManager:
    return ChromaDBManager(
        persist_directory=tmp_path / "vectors",
        embedding_function=HashingEmbeddingFunction(),
    )


@pytest.fixture(autouse=True)
def no_key_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rag_chain_module, "get_settings", lambda: AppSettings())


# The question (QUESTION in test_rag_answers) is about "quokka zephyr
# calibration drift". Pages 6 and 3 share those words, page 6 more of them, so
# retrieval returns [6, 3] and everything else shares nothing with the question.
# Each page carries a marker word that appears nowhere else, so the prompt shows
# which pages reached the model.
MARKERS = ["marigold", "nutmeg", "tarragon", "saffron", "juniper", "cardamom", "fennel", "sorrel"]
_BEST, _SECOND = 6, 3  # 1-indexed
_FILLER = "lorem ipsum dolor sit amet consectetur"


def _page_text(number: int) -> str:
    marker = MARKERS[number - 1]
    if number == _BEST:
        return f"quokka zephyr calibration drift quokka zephyr calibration drift {marker}"
    if number == _SECOND:
        return f"quokka zephyr calibration {marker} filler words about nothing"
    return f"{_FILLER} {marker} page {number}"


def eight_pages(overrides: Dict[int, Dict] = None) -> List[Dict]:
    """Eight pages; `overrides` maps a 1-indexed page to extra entry fields."""
    overrides = overrides or {}
    return [{"text": _page_text(n), **overrides.get(n, {})} for n in range(1, 9)]


def total_chars(pages: List[Dict]) -> int:
    return sum(len(entry["text"]) for entry in pages)


def shown_pages(prompt: str) -> Dict[int, str]:
    """Page number -> the text under its header, from inside the fence, in the
    order the prompt lists them (dicts keep insertion order)."""
    ((_tag, inside),) = _FENCE.findall(prompt)
    parts = re.split(r"--- page (\d+)[^\n]*---", inside)
    return {int(number): body for number, body in zip(parts[1::2], parts[2::2])}


def only_call(translator: _Translator) -> str:
    (prompt, _system, _kwargs), = translator.calls
    return prompt


# ---------------------------------------------------------------------------
# when the whole document is shown
# ---------------------------------------------------------------------------


def test_a_short_document_puts_every_page_in_the_prompt_in_page_order(tmp_path, store):
    pages = eight_pages()
    translator = _Translator("ok")
    index, path = build_document(tmp_path, store, pages)
    chain = make_chain(store, translator, full_document_chars=total_chars(pages) + 100)

    ask(chain, index, path, max_pdf_sources=2)

    shown = shown_pages(only_call(translator))
    assert list(shown) == [1, 2, 3, 4, 5, 6, 7, 8]
    for number, body in shown.items():
        assert MARKERS[number - 1] in body, f"page {number}'s text is under its own header"


def test_a_document_of_exactly_the_limit_is_still_whole(tmp_path, store):
    """The rule is `<=`: a limit equal to the size is not 'too long'."""
    pages = eight_pages()
    translator = _Translator("ok")
    index, path = build_document(tmp_path, store, pages)
    chain = make_chain(store, translator, full_document_chars=total_chars(pages))

    ask(chain, index, path, max_pdf_sources=2)

    assert list(shown_pages(only_call(translator))) == [1, 2, 3, 4, 5, 6, 7, 8]


# ---------------------------------------------------------------------------
# when it is not
# ---------------------------------------------------------------------------
#
# Regression guards for the behaviour that must not change. Until the option
# exists they fail only on the constructor's TypeError; once it does they pin
# the old behaviour for the cases the option must leave alone.


def test_guard_a_document_over_the_limit_shows_only_the_retrieved_pages(tmp_path, store):
    pages = eight_pages()
    translator = _Translator("ok")
    index, path = build_document(tmp_path, store, pages)
    chain = make_chain(store, translator, full_document_chars=total_chars(pages) - 1)

    ask(chain, index, path, max_pdf_sources=2)

    shown = shown_pages(only_call(translator))
    assert sorted(shown) == [_SECOND, _BEST]
    prompt = only_call(translator)
    for number, marker in enumerate(MARKERS, start=1):
        assert (marker in prompt) == (number in (_SECOND, _BEST)), marker


def test_guard_zero_disables_whole_document_mode_however_small_the_document(tmp_path, store):
    translator = _Translator("ok")
    index, path = build_document(tmp_path, store, eight_pages())
    chain = make_chain(store, translator, full_document_chars=0)

    ask(chain, index, path, max_pdf_sources=2)

    assert sorted(shown_pages(only_call(translator))) == [_SECOND, _BEST]


# ---------------------------------------------------------------------------
# citations
# ---------------------------------------------------------------------------


def test_whole_mode_keeps_a_citation_of_a_page_retrieval_did_not_rank(tmp_path, store):
    """The model was shown page 8, so citing it is honest even though search
    never offered it."""
    pages = eight_pages()
    translator = _Translator("The appendix says so [p. 8].")
    index, path = build_document(tmp_path, store, pages)
    chain = make_chain(store, translator, full_document_chars=total_chars(pages) + 100)

    result = ask(chain, index, path, max_pdf_sources=2)

    assert "[p. 8](#page=8)" in result["answer"]
    eighth = [r for r in result["pdf_references"] if r["page"] == 8]
    assert len(eighth) == 1 and eighth[0]["cited"] is True


def test_whole_mode_still_removes_a_citation_of_a_page_outside_the_document(tmp_path, store):
    """Guard: widening what counts as shown must not turn off the check."""
    pages = eight_pages()
    translator = _Translator("Here [p. 8] but not there [p. 9].")
    index, path = build_document(tmp_path, store, pages)
    chain = make_chain(store, translator, full_document_chars=total_chars(pages) + 100)

    result = ask(chain, index, path, max_pdf_sources=2)

    assert "[p. 8](#page=8)" in result["answer"]
    assert "p. 9" not in result["answer"]
    assert "page=9" not in result["answer"]
    assert result["quality_metrics"]["invalid_citations"] == 1


# ---------------------------------------------------------------------------
# references
# ---------------------------------------------------------------------------


def test_whole_mode_references_are_cited_pages_then_uncited_retrieved_pages(tmp_path, store):
    """Cited 8 then 1 (neither retrieved), in that order; then the retrieved
    pages not cited, best first: 6 then 3. Pages 2, 4, 5 and 7 were only shown
    because the document is short, so they are not offered as sources."""
    pages = eight_pages()
    translator = _Translator("First [p. 8]. Then [p. 1]. Again [p. 8].")
    index, path = build_document(tmp_path, store, pages)
    chain = make_chain(store, translator, full_document_chars=total_chars(pages) + 100)

    result = ask(chain, index, path, max_pdf_sources=2)

    references = result["pdf_references"]
    assert [r["page"] for r in references] == [8, 1, _BEST, _SECOND]
    assert [r["cited"] for r in references] == [True, True, False, False]


def test_guard_whole_mode_with_no_citation_lists_only_the_retrieved_pages(tmp_path, store):
    pages = eight_pages()
    translator = _Translator("ok")
    index, path = build_document(tmp_path, store, pages)
    chain = make_chain(store, translator, full_document_chars=total_chars(pages) + 100)

    result = ask(chain, index, path, max_pdf_sources=2)

    assert [r["page"] for r in result["pdf_references"]] == [_BEST, _SECOND]
    assert not any(r["cited"] for r in result["pdf_references"])


# ---------------------------------------------------------------------------
# images
# ---------------------------------------------------------------------------


def test_guard_whole_mode_sends_no_image_of_a_visual_page_retrieval_did_not_return(tmp_path, store):
    """Page 2 is visual but unrelated to the question; page 6 is visual and
    retrieved. Showing the text of every page must not mean paying for the
    image of every figure."""
    pages = eight_pages({2: {"page_kind": "visual"}, 6: {"page_kind": "visual"}})
    translator = _Translator("ok")
    index, path = build_document(tmp_path, store, pages)
    chain = make_chain(store, translator, full_document_chars=total_chars(pages) + 100)

    ask(chain, index, path, max_pdf_sources=2)

    (_prompt, _system, kwargs), = translator.calls
    assert image_labels(kwargs) == ["[Image of page 6]"]


def test_guard_whole_mode_sends_at_most_max_images_all_from_retrieved_pages(tmp_path, store):
    """Three retrieved visual pages (1-3, best first) among eight visual ones,
    two allowed: the two best-ranked, never one of pages 4-8."""
    texts = [
        "quokka zephyr calibration drift quokka zephyr calibration drift",
        "quokka zephyr calibration filler words about nothing",
        "quokka zephyr something else entirely unrelated words",
    ] + [f"{_FILLER} {MARKERS[n]} page {n + 1}" for n in range(3, 8)]
    pages = [{"text": t, "page_kind": "visual"} for t in texts]
    translator = _Translator("ok")
    index, path = build_document(tmp_path, store, pages)
    chain = make_chain(
        store, translator, max_images=2, full_document_chars=total_chars(pages) + 100
    )

    ask(chain, index, path, max_pdf_sources=3)

    (_prompt, _system, kwargs), = translator.calls
    assert image_labels(kwargs) == ["[Image of page 1]", "[Image of page 2]"]


# ---------------------------------------------------------------------------
# quality metrics
# ---------------------------------------------------------------------------


def test_pages_read_counts_every_page_in_whole_mode(tmp_path, store):
    pages = eight_pages()
    translator = _Translator("ok")
    index, path = build_document(tmp_path, store, pages)
    chain = make_chain(store, translator, full_document_chars=total_chars(pages) + 100)

    result = ask(chain, index, path, max_pdf_sources=2)

    assert result["quality_metrics"]["pages_read"] == 8


def test_pages_read_counts_the_retrieved_pages_otherwise(tmp_path, store):
    pages = eight_pages()
    translator = _Translator("ok")
    index, path = build_document(tmp_path, store, pages)
    chain = make_chain(store, translator, full_document_chars=0)

    result = ask(chain, index, path, max_pdf_sources=2)

    assert result["quality_metrics"]["pages_read"] == 2


# ---------------------------------------------------------------------------
# what bounds it: the model's window, and tokens rather than characters
# ---------------------------------------------------------------------------


def test_outside_whole_mode_a_citation_of_a_page_not_retrieved_is_dropped(tmp_path, store):
    """The citation rule itself: a page the model was not shown can't be a
    source, even one the document has."""
    pages = eight_pages()
    translator = _Translator("The appendix says so [p. 8].")
    index, path = build_document(tmp_path, store, pages)

    result = ask(make_chain(store, translator, full_document_chars=0), index, path, max_pdf_sources=2)

    assert "[p. 8]" not in result["answer"]
    assert result["quality_metrics"]["invalid_citations"] == 1
    assert all(r["page"] != 8 for r in result["pdf_references"])


def test_text_that_takes_a_token_a_character_counts_as_longer(tmp_path, store):
    """40,000 characters is ~10k tokens of English but ~40k of Chinese: the
    budget is in English-equivalent characters, so a CJK document that is
    short by character count is still too long to read whole."""
    cjk = "漢字文書" * 1500  # 6,000 characters, ~6,000 tokens
    pages = [{"text": f"{entry['text']} {cjk}"} for entry in eight_pages()]
    translator = _Translator("ok")
    index, path = build_document(tmp_path, store, pages)
    limit = total_chars(pages) + 100  # enough, if a character were a character

    ask(make_chain(store, translator, full_document_chars=limit), index, path, max_pdf_sources=2)

    assert len(shown_pages(only_call(translator))) == 2


def test_without_a_model_the_document_is_not_read_whole(tmp_path, store):
    """The extractive answer quotes retrieved passages; nothing reads the rest."""
    pages = eight_pages()
    index, path = build_document(tmp_path, store, pages)

    result = ask(make_chain(store, None, full_document_chars=10_000), index, path, max_pdf_sources=2)

    assert result["quality_metrics"]["pages_read"] == 2



# ---------------------------------------------------------------------------
# the budget is a setting
# ---------------------------------------------------------------------------


def _settings_with_budget(tokens: int):
    settings = AppSettings()
    settings.rag.whole_document_tokens = tokens
    return settings


@pytest.mark.parametrize(("tokens", "whole"), [(0, False), (11_500, True)])
def test_without_an_explicit_limit_the_saved_setting_decides(tmp_path, store, monkeypatch, tokens, whole):
    """Read per question, like the answer model, so a change applies to the
    next question without restarting the sidecar."""
    monkeypatch.setattr(rag_chain_module, "get_settings", lambda: _settings_with_budget(tokens))
    pages = eight_pages()
    translator = _Translator("ok")
    index, path = build_document(tmp_path, store, pages)

    ask(make_chain(store, translator), index, path, max_pdf_sources=2)

    assert len(shown_pages(only_call(translator))) == (8 if whole else 2)


def test_a_changed_setting_applies_to_the_next_question_on_the_same_chain(tmp_path, store, monkeypatch):
    """The chain is built once per sidecar (`api/routes/rag.py`); a setting
    read when it was built would ignore every later change."""
    current = {"settings": _settings_with_budget(0)}
    monkeypatch.setattr(rag_chain_module, "get_settings", lambda: current["settings"])
    translator = _Translator("ok")
    index, path = build_document(tmp_path, store, eight_pages())
    chain = make_chain(store, translator)

    ask(chain, index, path, max_pdf_sources=2)
    current["settings"] = _settings_with_budget(11_500)
    ask(chain, index, path, max_pdf_sources=2)

    first, second = (shown_pages(prompt) for prompt, _system, _kwargs in translator.calls)
    assert (len(first), len(second)) == (2, 8)


def test_the_token_budget_converts_at_three_and_a_half_characters_a_token(tmp_path, store, monkeypatch):
    """English runs ~3.5 characters a token (`rag_chain._CHARS_PER_TOKEN`): a
    document fits a budget of N tokens when its text is at most 3.5 × N
    characters, and not one token less."""
    import math

    pages = eight_pages()
    fits = math.ceil(total_chars(pages) / 3.5)
    index, path = build_document(tmp_path, store, pages)
    shown = []
    for tokens in (fits, fits - 1):
        monkeypatch.setattr(rag_chain_module, "get_settings", lambda t=tokens: _settings_with_budget(t))
        translator = _Translator("ok")
        ask(make_chain(store, translator), index, path, max_pdf_sources=2)
        shown.append(len(shown_pages(only_call(translator))))

    assert shown == [8, 2]


# ---------------------------------------------------------------------------
# the provider refuses a whole read as too long (#106)
# ---------------------------------------------------------------------------
#
# The budget is an estimate (`_CHARS_PER_TOKEN`), and Vietnamese or CJK text
# runs past it, so a whole read can still overflow a small window. The request
# is rejected, and every later question on that document would fail the same
# way; the retrieved pages always fit, so the question is asked again with those.

# One message per provider family; the status is what each really sends.
_TOO_LONG = [
    pytest.param(
        400,
        "This model's maximum context length is 131072 tokens. However, you "
        "requested 150000 tokens in the messages.",
        id="openai-deepseek-openrouter",
    ),
    pytest.param(
        400,
        "Error code: 400 - {'error': {'code': 'context_length_exceeded'}}",
        id="openai-code",
    ),
    pytest.param(400, "prompt is too long: 210000 tokens > 200000 maximum", id="anthropic"),
    pytest.param(
        400,
        "The input token count (1200000) exceeds the maximum number of tokens allowed (1048576).",
        id="gemini",
    ),
    pytest.param(422, "Maximum Context Length exceeded", id="422-any-case"),
    pytest.param(413, "Payload Too Large", id="413-whatever-the-message"),
    pytest.param(413, "", id="413-no-message"),
]


@pytest.mark.parametrize(("status", "message"), _TOO_LONG)
def test_a_whole_read_the_provider_calls_too_long_is_asked_again_from_the_retrieved_pages(
    tmp_path, store, status, message
):
    pages = eight_pages()
    translator = _Translator(_SdkError(status, message), "Drift is calibrated [p. 6].")
    index, path = build_document(tmp_path, store, pages)
    chain = make_chain(store, translator, full_document_chars=total_chars(pages) + 100)

    result = ask(chain, index, path, max_pdf_sources=2)

    assert len(translator.calls) == 2
    assert "[p. 6](#page=6)" in result["answer"]
    assert result["quality_metrics"]["pages_read"] == 2


def test_the_second_read_shows_only_the_retrieved_pages(tmp_path, store):
    """The same question, so the retry changes only how much of the document
    goes with it."""
    pages = eight_pages()
    translator = _Translator(_SdkError(400, "maximum context length is 8192 tokens"), "ok")
    index, path = build_document(tmp_path, store, pages)
    chain = make_chain(store, translator, full_document_chars=total_chars(pages) + 100)

    ask(chain, index, path, max_pdf_sources=2)

    (whole, _s1, _k1), (retrieved, _s2, _k2) = translator.calls
    assert sorted(shown_pages(whole)) == [1, 2, 3, 4, 5, 6, 7, 8]
    assert sorted(shown_pages(retrieved)) == [_SECOND, _BEST]
    for number, marker in enumerate(MARKERS, start=1):
        assert (marker in retrieved) == (number in (_SECOND, _BEST)), marker


def test_a_citation_only_the_rejected_read_could_have_made_is_dropped(tmp_path, store):
    """The model that answers saw pages 3 and 6; page 8 was in the request that
    was refused, so citing it would point at a page nobody read."""
    pages = eight_pages()
    translator = _Translator(
        _SdkError(400, "prompt is too long: 300000 tokens > 200000 maximum"),
        "The appendix says so [p. 8], the drift is in [p. 6].",
    )
    index, path = build_document(tmp_path, store, pages)
    chain = make_chain(store, translator, full_document_chars=total_chars(pages) + 100)

    result = ask(chain, index, path, max_pdf_sources=2)

    assert "p. 8" not in result["answer"]
    assert "page=8" not in result["answer"]
    assert "[p. 6](#page=6)" in result["answer"]
    assert result["quality_metrics"]["invalid_citations"] == 1
    assert all(r["page"] != 8 for r in result["pdf_references"])


@pytest.mark.parametrize(
    ("status", "message"),
    [
        pytest.param(
            429,
            "Request too large for gpt-4o in organization org-x on tokens per min "
            "(TPM): Limit 30000, Requested 45000.",
            id="rate-limit-wording",
        ),
        pytest.param(400, "Invalid request: temperature is out of range", id="unrelated-400"),
        pytest.param(500, "maximum context length", id="not-a-client-error"),
    ],
)
def test_guard_an_error_that_is_not_about_length_does_not_trigger_the_retry(
    tmp_path, store, status, message
):
    """A rate limit says "too large" too, but waiting fixes it, and reading
    fewer pages would only hide it."""
    pages = eight_pages()
    translator = _Translator(_SdkError(status, message))
    index, path = build_document(tmp_path, store, pages)
    chain = make_chain(store, translator, full_document_chars=total_chars(pages) + 100)

    with pytest.raises(AnswerGenerationError):
        ask(chain, index, path, max_pdf_sources=2)

    assert len(translator.calls) == 1


def test_guard_a_retrieved_read_the_provider_calls_too_long_has_nothing_smaller_to_try(
    tmp_path, store
):
    pages = eight_pages()
    translator = _Translator(_SdkError(400, "maximum context length is 8192 tokens"))
    index, path = build_document(tmp_path, store, pages)
    chain = make_chain(store, translator, full_document_chars=0)

    with pytest.raises(AnswerGenerationError):
        ask(chain, index, path, max_pdf_sources=2)

    assert len(translator.calls) == 1


def test_a_second_refusal_after_the_retry_fails_the_question(tmp_path, store):
    """One retry, not a loop: the retrieved read overflowing too is an error."""
    pages = eight_pages()
    translator = _Translator(_SdkError(400, "maximum context length is 8192 tokens"))
    index, path = build_document(tmp_path, store, pages)
    chain = make_chain(store, translator, full_document_chars=total_chars(pages) + 100)

    with pytest.raises(AnswerGenerationError):
        ask(chain, index, path, max_pdf_sources=2)

    assert len(translator.calls) == 2
