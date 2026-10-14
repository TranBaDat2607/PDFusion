"""`generate(images=...)` shows a vision model the rendered PDF pages.

The chat sends page images ahead of its question, so each LLM backend has to
put them into its SDK's request in that SDK's own shape: images (each after
its label, when it has one) first, the prompt last. These tests replace the
SDK client with a fake that records the request, so nothing touches the network.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from google.genai import types as genai_types

from desktop_pdf_translator.translators import (
    anthropic_translator,
    gemini_translator,
    openai_translator,
    rate_limiter,
)
from desktop_pdf_translator.translators.anthropic_translator import AnthropicTranslator
from desktop_pdf_translator.translators.base import PageImage
from desktop_pdf_translator.translators.gemini_translator import GeminiTranslator
from desktop_pdf_translator.translators.openai_translator import OpenAITranslator

PROMPT = "What does the figure show?"
SYSTEM = "Answer from the pages."
JPEG_BYTES = b"\xff\xd8\xff-first-page-\x00\x01"
PNG_BYTES = b"\x89PNG\r\n\x1a\n-second-page-\x00\xff"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


@pytest.fixture(autouse=True)
def _fresh_state():
    rate_limiter._LIMITERS.clear()
    yield
    rate_limiter._LIMITERS.clear()


class _OpenAIBackend:
    name = "openai"

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []
        self.translator = OpenAITranslator("en", "vi", api_key="sk-test", model="gpt-test")
        completions = SimpleNamespace(create=self._create)
        self.translator.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content="  the answer  ")
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])

    def user_content(self):
        return self.calls[0]["messages"][-1]["content"]

    def system_sent(self):
        first = self.calls[0]["messages"][0]
        return first["content"] if first["role"] == "system" else None

    def text(self, text):
        return {"type": "text", "text": text}

    def image(self, image):
        url = f"data:{image.mime_type};base64,{_b64(image.data)}"
        return {"type": "image_url", "image_url": {"url": url}}


class _AnthropicBackend:
    name = "anthropic"

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []
        self.translator = AnthropicTranslator("en", "vi", api_key="sk-test", model="claude-test")
        self.translator.client = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="  the answer  ")],
            stop_reason="end_turn",
        )

    def user_content(self):
        return self.calls[0]["messages"][0]["content"]

    def system_sent(self):
        return self.calls[0].get("system")

    def text(self, text):
        return {"type": "text", "text": text}

    def image(self, image):
        source = {"type": "base64", "media_type": image.mime_type, "data": _b64(image.data)}
        return {"type": "image", "source": source}


class _GeminiBackend:
    name = "gemini"

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []
        self.translator = GeminiTranslator("en", "vi", api_key="key-test", model="gemini-test")
        self.translator.client = SimpleNamespace(models=SimpleNamespace(generate_content=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(text="  the answer  ", candidates=[])

    def user_content(self):
        """Parts as plain values: a string stays a string, a Part becomes a
        tuple, so a wrong request compares unequal instead of raising."""
        contents = self.calls[0]["contents"]
        if not isinstance(contents, list):
            return contents
        return [
            ("image", p.inline_data.mime_type, p.inline_data.data)
            if isinstance(p, genai_types.Part)
            else p
            for p in contents
        ]

    def system_sent(self):
        return self.calls[0]["config"].system_instruction

    def text(self, text):
        return text

    def image(self, image):
        return ("image", image.mime_type, image.data)


BACKENDS = [_OpenAIBackend, _AnthropicBackend, _GeminiBackend]


@pytest.fixture(params=BACKENDS, ids=lambda cls: cls.name)
def backend(request, monkeypatch: pytest.MonkeyPatch):
    # The paragraph cache is a translate() concern; generate() never reads it.
    for module in (openai_translator, anthropic_translator, gemini_translator):
        monkeypatch.setattr(module, "_llm_cache_get", lambda *a, **k: None)
        monkeypatch.setattr(module, "_llm_cache_set", lambda *a, **k: None)
    return request.param()


JPEG = PageImage(data=JPEG_BYTES, mime_type="image/jpeg", label="Page 3")
PNG = PageImage(data=PNG_BYTES, mime_type="image/png", label="Page 7")


def test_without_images_the_prompt_is_sent_as_plain_text(backend):
    """A guard, not a red test: it passes against the stub because today's
    request shape is what `images=None` and `images=[]` must keep sending."""
    for images in (None, []):
        backend.calls.clear()
        backend.translator.generate(PROMPT, system=SYSTEM, images=images)
        assert backend.user_content() == PROMPT
        assert backend.system_sent() == SYSTEM


def test_a_labelled_image_goes_before_the_prompt_after_its_label(backend):
    backend.translator.generate(PROMPT, images=[JPEG])

    assert backend.user_content() == [
        backend.text("Page 3"),
        backend.image(JPEG),
        backend.text(PROMPT),
    ]


def test_images_keep_their_order_each_after_its_own_label(backend):
    backend.translator.generate(PROMPT, images=[PNG, JPEG])

    assert backend.user_content() == [
        backend.text("Page 7"),
        backend.image(PNG),
        backend.text("Page 3"),
        backend.image(JPEG),
        backend.text(PROMPT),
    ]


def test_an_unlabelled_image_has_no_text_part_before_it(backend):
    bare = PageImage(data=JPEG_BYTES)

    backend.translator.generate(PROMPT, images=[bare])

    assert backend.user_content() == [backend.image(bare), backend.text(PROMPT)]


def test_a_png_keeps_its_mime_type(backend):
    bare_png = PageImage(data=PNG_BYTES, mime_type="image/png")

    backend.translator.generate(PROMPT, images=[bare_png])

    assert backend.user_content() == [backend.image(bare_png), backend.text(PROMPT)]


def test_the_system_prompt_is_still_sent_with_images(backend):
    backend.translator.generate(PROMPT, system=SYSTEM, images=[JPEG])

    assert backend.user_content() == [
        backend.text("Page 3"),
        backend.image(JPEG),
        backend.text(PROMPT),
    ]
    assert backend.system_sent() == SYSTEM


def test_the_generated_text_is_returned_when_images_are_sent(backend):
    answer = backend.translator.generate(PROMPT, images=[JPEG])

    assert answer == "the answer"
    assert backend.user_content() != PROMPT, "the images never reached the request"
