"""The chat remembers the conversation it is having about one document.

A follow-up such as "And in Asia-Pacific?" or "When is it payable?" names
nothing the document says, so searched and answered on its own it retrieves
badly. The answer model is first asked to restate the question standalone and
retrieval searches with that; the answer prompt carries the last few
exchanges so "it" and "that" resolve. Without an answer model there is nobody
to rewrite, so the search simply uses the previous question as well.

Real ChromaDB under `tmp_path` and a deterministic embedding function, as in
`test_rag_answers.py`, whose fakes this file reuses.
"""

from __future__ import annotations

import re

import asyncio
from pathlib import Path
from typing import Any, Dict, List

import pytest

from desktop_pdf_translator.config import AppSettings
from desktop_pdf_translator.rag import rag_chain as rag_chain_module
from desktop_pdf_translator.rag.rag_chain import ChatTurn, EnhancedRAGChain
from desktop_pdf_translator.rag.vector_store import ChromaDBManager
from test_rag_answers import (
    DOCUMENT_ID,
    HashingEmbeddingFunction,
    _Translator,
    build_document,
    make_chain,
)

# The follow-up shares not one content word with the page that answers it, and
# the distractor page is made of exactly its words: searched raw, the
# distractor wins. Only the conversation can put the right page first.
FOLLOW_UP = "And what about that one?"
PREVIOUS_QUESTION = "Tell me about the marmoset lantern harvest schedule cinnabar orchard kestrel"
PREVIOUS_ANSWER = "The harvest runs through autumn."
REWRITE = "Explain the ocelot vermilion tariff payable quarterly invoice ledger"

DISTRACTOR_TEXT = "and what about that one anyway"
PREVIOUS_TOPIC_TEXT = "marmoset lantern harvest schedule cinnabar orchard kestrel"
TARGET_TEXT = "ocelot vermilion tariff payable quarterly invoice ledger"
FILLER_TEXTS = [
    "zinnia quartz glacier mosaic",
    "umber fjord lattice pylon",
]
PAGES = [{"text": t} for t in
         [DISTRACTOR_TEXT, PREVIOUS_TOPIC_TEXT, TARGET_TEXT, *FILLER_TEXTS]]
DISTRACTOR_PAGE, PREVIOUS_TOPIC_PAGE, TARGET_PAGE = 0, 1, 2  # 0-indexed

HISTORY = (ChatTurn(PREVIOUS_QUESTION, PREVIOUS_ANSWER),)
ANSWER = "It is payable quarterly [p. 3]."


@pytest.fixture
def store(tmp_path: Path) -> ChromaDBManager:
    return ChromaDBManager(
        persist_directory=tmp_path / "vectors",
        embedding_function=HashingEmbeddingFunction(),
    )


@pytest.fixture(autouse=True)
def no_key_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rag_chain_module, "get_settings", lambda: AppSettings())


def ask(chain: EnhancedRAGChain, index_id: str, path: Path, question: str = FOLLOW_UP,
        **kwargs) -> Dict[str, Any]:
    return asyncio.run(
        chain.answer_question(
            question=question,
            index_id=index_id,
            document_id=DOCUMENT_ID,
            document_path=str(path),
            answer_lang=kwargs.pop("answer_lang", "en"),
            **kwargs,
        )
    )


def retrieve_pages(chain: EnhancedRAGChain, index_id: str, question: str = FOLLOW_UP,
                   max_pages: int = 3, model=None, **kwargs) -> List[int]:
    evidence = asyncio.run(chain.retrieve(question, index_id, max_pages, model=model, **kwargs))
    return [page.page for page in evidence]


def text_of(call) -> str:
    prompt, system, _kwargs = call
    return prompt + (system or "")


def numbered_turns(count: int) -> List[ChatTurn]:
    return [ChatTurn(f"QUESTIONMARK{n} about harvest", f"ANSWERMARK{n} runs in autumn")
            for n in range(1, count + 1)]


# ---------------------------------------------------------------------------
# no history: nothing changes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extra", [{}, {"history": ()}, {"history": []}])
def test_without_history_there_is_one_model_call_and_no_earlier_exchange(tmp_path, store, extra):
    translator = _Translator(ANSWER)
    index, path = build_document(tmp_path, store, PAGES)

    ask(make_chain(store, translator, hyde=False), index, path, **extra)

    (prompt, _system, _), = translator.calls
    assert FOLLOW_UP in prompt
    assert PREVIOUS_QUESTION not in prompt


def test_an_empty_history_builds_the_same_prompt_as_none(tmp_path, store):
    """The keyword is optional on the wire; passing nothing must not be a
    different request from passing an empty list."""
    index, path = build_document(tmp_path, store, PAGES)
    plain, empty = _Translator(ANSWER), _Translator(ANSWER)

    ask(make_chain(store, plain, hyde=False), index, path)
    ask(make_chain(store, empty, hyde=False), index, path, history=())

    # Each question fences its pages with a tag of its own
    # (`test_each_question_gets_a_tag_of_its_own`); everything else must match.
    def untagged(prompt):
        return re.sub(r"document-\w+", "document-TAG", prompt)

    assert untagged(plain.calls[0][0]) == untagged(empty.calls[0][0])


def test_the_limit_is_three_turns():
    assert rag_chain_module.MAX_HISTORY_TURNS == 3


# ---------------------------------------------------------------------------
# the rewrite
# ---------------------------------------------------------------------------


def test_with_history_the_model_is_asked_to_rewrite_first_and_then_answers(tmp_path, store):
    translator = _Translator(REWRITE, ANSWER)
    index, path = build_document(tmp_path, store, PAGES)

    result = ask(make_chain(store, translator, hyde=False), index, path, history=HISTORY)

    rewrite_call, answer_call = translator.calls
    rewrite_text = text_of(rewrite_call)
    for expected in (PREVIOUS_QUESTION, PREVIOUS_ANSWER, FOLLOW_UP):
        assert expected in rewrite_text
    assert "[p. N]" not in rewrite_text, "the first call is the rewrite, not the answer"
    assert "[p. N]" in text_of(answer_call)
    assert "payable quarterly" in result["answer"]


def test_retrieval_searches_with_the_rewrite(tmp_path, store):
    """With one page of evidence the answer prompt shows what was found: the
    page only the rewrite's words reach, not the one the raw follow-up
    matches."""
    translator = _Translator(REWRITE, ANSWER)
    index, path = build_document(tmp_path, store, PAGES)

    # Retrieval is what's under test, seen through the pages the prompt gets;
    # this document is short enough to be read whole otherwise.
    ask(make_chain(store, translator, hyde=False, full_document_chars=0), index, path,
        history=HISTORY, max_pdf_sources=1)

    answer_prompt = translator.calls[1][0]
    assert TARGET_TEXT in answer_prompt
    assert DISTRACTOR_TEXT not in answer_prompt


def test_the_follow_up_alone_would_not_find_the_page(tmp_path, store):
    """Guards the fixture: if the raw follow-up already ranked the target
    first, the tests above would prove nothing."""
    index, _path = build_document(tmp_path, store, PAGES)

    pages = retrieve_pages(make_chain(store, None), index, max_pages=1)

    assert pages == [DISTRACTOR_PAGE]


def test_the_rewrite_carries_no_answer_language_instruction(tmp_path, store):
    """A rewrite in the answer's language would stop matching the document's
    words."""
    translator = _Translator(REWRITE, ANSWER)
    index, path = build_document(tmp_path, store, PAGES)

    ask(make_chain(store, translator, hyde=False), index, path,
        history=HISTORY, answer_lang="ja")

    rewrite_call, answer_call = translator.calls
    assert "Japanese" not in text_of(rewrite_call)
    assert "Japanese" in text_of(answer_call)


@pytest.mark.parametrize("failure", [RuntimeError("boom"), ""], ids=["raises", "empty"])
def test_a_failed_rewrite_still_gets_the_question_answered(tmp_path, store, failure):
    translator = _Translator(failure, ANSWER)
    index, path = build_document(tmp_path, store, PAGES)

    result = ask(make_chain(store, translator, hyde=False), index, path, history=HISTORY)

    assert result["answer"].startswith("It is payable quarterly")
    answer_call = translator.calls[-1]
    assert "[p. N]" in text_of(answer_call)
    assert FOLLOW_UP in answer_call[0]


# ---------------------------------------------------------------------------
# the answer prompt
# ---------------------------------------------------------------------------


def test_the_answer_prompt_shows_the_earlier_exchange_before_the_new_question(tmp_path, store):
    translator = _Translator(REWRITE, ANSWER)
    index, path = build_document(tmp_path, store, PAGES)

    ask(make_chain(store, translator, hyde=False), index, path, history=HISTORY)

    prompt = translator.calls[1][0]
    asked, answered = prompt.find(PREVIOUS_QUESTION), prompt.find(PREVIOUS_ANSWER)
    last_question = prompt.rfind("Question:")
    assert -1 not in (asked, answered, last_question)
    assert asked < answered < last_question
    assert FOLLOW_UP in prompt[last_question:], "the last Question: is the new one"


def test_only_the_last_three_turns_reach_either_prompt(tmp_path, store):
    translator = _Translator(REWRITE, ANSWER)
    index, path = build_document(tmp_path, store, PAGES)

    ask(make_chain(store, translator, hyde=False), index, path, history=numbered_turns(5))

    for call in translator.calls:
        text = text_of(call)
        for dropped in (1, 2):
            assert f"QUESTIONMARK{dropped} " not in text
            assert f"ANSWERMARK{dropped} " not in text
        for kept in (3, 4, 5):
            assert f"QUESTIONMARK{kept} " in text
            assert f"ANSWERMARK{kept} " in text


def test_a_long_earlier_answer_is_truncated_in_the_prompts(tmp_path, store):
    long_answer = "BEGINMARK " + "x" * 5000 + " ENDMARK"
    translator = _Translator(REWRITE, ANSWER)
    index, path = build_document(tmp_path, store, PAGES)

    ask(make_chain(store, translator, hyde=False), index, path,
        history=[ChatTurn(PREVIOUS_QUESTION, long_answer)])

    for call in translator.calls:
        text = text_of(call)
        assert "BEGINMARK" in text
        assert "ENDMARK" not in text
        assert "x" * 5000 not in text


# ---------------------------------------------------------------------------
# no answer model: the search itself carries the conversation
# ---------------------------------------------------------------------------


def test_without_an_answer_model_the_previous_question_joins_the_search(tmp_path, store):
    """Only the previous question's words reach the topic page; the follow-up's
    own words match the distractor."""
    index, _path = build_document(tmp_path, store, PAGES)
    previous = ChatTurn(PREVIOUS_QUESTION, PREVIOUS_ANSWER)

    pages = retrieve_pages(make_chain(store, None), index, FOLLOW_UP, max_pages=1,
                           history=[previous])

    assert pages == [PREVIOUS_TOPIC_PAGE]


# ---------------------------------------------------------------------------
# retrieve() on its own follows the same rules
# ---------------------------------------------------------------------------


def test_retrieve_searches_with_the_rewrite(tmp_path, store):
    translator = _Translator(REWRITE)
    index, _path = build_document(tmp_path, store, PAGES)
    chain = make_chain(store, translator, hyde=False)

    pages = retrieve_pages(chain, index, max_pages=1, model=chain._answer_model(),
                           history=HISTORY)

    assert pages == [TARGET_PAGE]
    assert len(translator.calls) == 1


@pytest.mark.parametrize("failure", [RuntimeError("boom"), ""], ids=["raises", "empty"])
def test_retrieve_survives_a_failed_rewrite(tmp_path, store, failure):
    translator = _Translator(failure)
    index, _path = build_document(tmp_path, store, PAGES)
    chain = make_chain(store, translator, hyde=False)

    pages = retrieve_pages(chain, index, max_pages=3, model=chain._answer_model(),
                           history=HISTORY)

    assert pages, "the search still ran"


def test_retrieve_without_a_model_uses_the_previous_question(tmp_path, store):
    index, _path = build_document(tmp_path, store, PAGES)
    previous = ChatTurn(PREVIOUS_QUESTION, PREVIOUS_ANSWER)

    pages = retrieve_pages(make_chain(store, None), index, max_pages=1, model=None,
                           history=(previous,))

    assert pages == [PREVIOUS_TOPIC_PAGE]


# ---------------------------------------------------------------------------
# what the answer says about how it was found
# ---------------------------------------------------------------------------


def test_the_answer_reports_the_question_the_search_used(tmp_path, store):
    """A follow-up is searched for as its rewrite; when the answer is wrong,
    the rewrite is the first suspect, so the answer carries it."""
    index, path = build_document(tmp_path, store, PAGES)
    translator = _Translator(REWRITE, ANSWER)

    result = ask(make_chain(store, translator, hyde=False), index, path, history=HISTORY)

    assert result["search_query"] == REWRITE


def test_a_question_searched_as_typed_reports_no_rewrite(tmp_path, store):
    index, path = build_document(tmp_path, store, PAGES)

    result = ask(make_chain(store, _Translator(ANSWER), hyde=False), index, path)

    assert result["search_query"] is None


def test_the_answer_reports_how_long_search_and_writing_took(tmp_path, store):
    index, path = build_document(tmp_path, store, PAGES)

    result = ask(make_chain(store, _Translator(ANSWER), hyde=False), index, path)

    metrics = result["quality_metrics"]
    assert metrics["retrieve_seconds"] >= 0
    assert metrics["answer_seconds"] >= 0


@pytest.mark.parametrize("failure", [RuntimeError("boom"), ""], ids=["raises", "empty"])
def test_a_failed_rewrite_searches_with_the_question_as_typed(tmp_path, store, failure):
    """Not the previous question joined to it — that is the no-model path —
    and not nothing: the question as typed, and the answer says no rewrite
    was used."""
    translator = _Translator(failure, ANSWER)
    index, path = build_document(tmp_path, store, PAGES)

    # Retrieval is what's under test, seen through the pages the prompt gets;
    # this document is short enough to be read whole otherwise.
    result = ask(make_chain(store, translator, hyde=False, full_document_chars=0), index, path,
                 history=HISTORY, max_pdf_sources=1)

    assert result["search_query"] is None
    # The evidence is what is fenced in; the conversation outside the fence
    # names the previous topic whatever was searched.
    (pages,) = re.findall(r"<(document-\w+)>(.*?)</\1>", translator.calls[-1][0], re.DOTALL)
    assert DISTRACTOR_TEXT in pages[1]
    assert PREVIOUS_TOPIC_TEXT not in pages[1]
