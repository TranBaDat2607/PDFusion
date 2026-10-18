"""
Google Gemini translator implementation with Vietnamese optimization.
"""

import logging
import re
from typing import Optional, Sequence

from google import genai
from google.genai import types as genai_types

from ..providers.registry import provider
from .base import BaseTranslator, OnText, PageImage, TranslationCancelled, deliver_stream, generate_timeout_s
from .translation_cache import llm_cache_get as _llm_cache_get, llm_cache_set as _llm_cache_set


logger = logging.getLogger(__name__)

# `**word**` and `*word*`: the marker hugs the words inside it and has a
# space or punctuation outside it. Without the outer guards, `x**2 + y**2`
# read as bold and `{v1}*{v2}*{v3}` (formula placeholders) as italic; a
# lone `*` (`2 * 3`, a footnote's `nghĩa*`) never pairs at all.
_BOLD = re.compile(r"(?<![\w*{}])\*\*(?=\S)(.+?)(?<=\S)\*\*(?![\w*{}])")
_ITALIC = re.compile(
    r"(?<![\w*{}])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![\w*{}])"
)


def _cut_short(response) -> bool:
    """Whether the reply hit `max_output_tokens`, which a thinking model's
    thinking counts toward, so what came back can end mid-sentence."""
    candidates = getattr(response, "candidates", None) or []
    reason = getattr(candidates[0], "finish_reason", None) if candidates else None
    return getattr(reason, "name", reason) == "MAX_TOKENS"


class GeminiTranslator(BaseTranslator):
    """
    Google Gemini-based translator with Vietnamese language optimization.

    Supports Gemini Pro and other Google AI models with special handling
    for Vietnamese language translations.
    """

    _SERVICE_NAME = "gemini"

    def __init__(self, lang_in: str, lang_out: str, **kwargs):
        super().__init__(lang_in, lang_out, **kwargs)

    def _setup_translator(self, **kwargs):
        self.api_key = kwargs.get("api_key")
        if not self.api_key:
            raise ValueError("Gemini API key is required")

        self.model_name = kwargs.get("model") or provider("gemini").default_model
        self.temperature = kwargs.get("temperature", 0.3)

        # Set once here rather than per-GenerateContentConfig so it also
        # covers validate_configuration — without it, google-genai passes no
        # timeout to httpx at all, and a hung call blocks this worker thread
        # indefinitely, past what a cancel check can catch. generate() sets its
        # own per request (`_request_options`).
        self.client = genai.Client(
            api_key=self.api_key,
            http_options=genai_types.HttpOptions(timeout=30_000),
        )
        self.generation_config = genai_types.GenerateContentConfig(
            temperature=self.temperature,
            max_output_tokens=4000,
            candidate_count=1,
            safety_settings=[
                genai_types.SafetySetting(category="HARM_CATEGORY_HARASSMENT",        threshold="BLOCK_MEDIUM_AND_ABOVE"),
                genai_types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH",       threshold="BLOCK_MEDIUM_AND_ABOVE"),
                genai_types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_MEDIUM_AND_ABOVE"),
                genai_types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_MEDIUM_AND_ABOVE"),
            ],
        )

        logger.info(f"Gemini translator configured with model: {self.model_name}")
    
    def translate(self, text: str, **kwargs) -> str:
        self._note_translate_call()
        if self.is_cancelled():
            return text

        try:
            processed_text = self._preprocess_text(text)
            if not processed_text.strip():
                return text

            cached = _llm_cache_get(processed_text, self.lang_in, self.lang_out, "gemini", self.model_name)
            if cached is not None:
                self._fire_paragraph_callback(processed_text, cached)
                return cached

            response = self._call_with_backoff(
                lambda: self.client.models.generate_content(
                    model=self.model_name,
                    contents=self._create_translation_prompt(processed_text),
                    config=self.generation_config,
                ),
            )

            if not (response.candidates and response.candidates[0].content):
                # A safety filter drops `candidates` entirely. That is a failed
                # paragraph like any other: without the funnel it came back as
                # source text, uncounted, and the run stayed cacheable.
                return self._handle_translation_error(
                    RuntimeError(
                        "Gemini returned no candidates (response filtered or "
                        "empty)"
                    ),
                    text,
                )

            if _cut_short(response):
                # Taken as whole, it would be cached and printed into the PDF.
                return self._handle_translation_error(
                    RuntimeError("Gemini stopped at its token limit"), text
                )

            translated_text = response.text.strip()
            if not translated_text:
                return self._handle_translation_error(
                    RuntimeError("Gemini returned an empty translation"), text
                )

            result = self._postprocess_text(translated_text)
            _llm_cache_set(processed_text, result, self.lang_in, self.lang_out, "gemini", self.model_name)
            self._fire_paragraph_callback(processed_text, result)
            return result

        except TranslationCancelled:
            return text
        except Exception as e:
            return self._handle_translation_error(e, text)

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 1000,
        images: Optional[Sequence[PageImage]] = None,
        on_text: Optional[OnText] = None,
    ) -> Optional[str]:
        """Freeform generation used by the RAG chain.

        Raises on failure, after `_call_with_backoff`'s retries; see
        `OpenAITranslator.generate`.
        """
        config = genai_types.GenerateContentConfig(
            temperature=0.3,
            max_output_tokens=max_tokens,
            system_instruction=system if system else None,
            http_options=self._request_options(generate_timeout_s(prompt, system, images)),
        )
        if on_text is not None:
            return self._generate_streaming(prompt, images, config, on_text)
        response = self._call_with_backoff(
            lambda: self.client.models.generate_content(
                model=self.model_name,
                contents=self._contents(prompt, images),
                config=config,
            )
        )
        if _cut_short(response):
            # Raised, so the chat shows an error rather than a cut-off answer.
            raise RuntimeError("Gemini stopped at its token limit")
        text = response.text
        return text.strip() if text else None

    @staticmethod
    def _request_options(seconds: float):
        """Per request: the client-wide 30 s is right for a paragraph and far
        too short for a whole-document prompt (#110). The SDK also sends the
        timeout as `X-Server-Timeout` and keeps one already on the client's
        headers, which a client that once made a 30 s request has; naming it
        here is what keeps the server's deadline in step with ours."""
        return genai_types.HttpOptions(
            timeout=int(seconds * 1000),
            headers={"X-Server-Timeout": str(int(seconds))},
        )

    def _generate_streaming(self, prompt: str, images: Optional[Sequence[PageImage]],
                            config, on_text: OnText) -> Optional[str]:
        def request():
            # The SDK's stream fails lazily, on iteration: read to its end
            # inside the retried request, so a dropped stream is retried too.
            chunks = self.client.models.generate_content_stream(
                model=self.model_name,
                contents=self._contents(prompt, images),
                config=config,
            )
            cut = [False]

            def pieces():
                for chunk in chunks:
                    cut[0] = cut[0] or _cut_short(chunk)
                    yield getattr(chunk, "text", None)

            return deliver_stream(pieces(), on_text), cut[0]

        text, cut = self._call_with_backoff(request)
        if cut:
            raise RuntimeError("Gemini stopped at its token limit")
        return text.strip() or None

    @staticmethod
    def _contents(prompt: str, images: Optional[Sequence[PageImage]]):
        """The prompt alone, as before images existed, or each image after
        its label and the prompt last."""
        if not images:
            return prompt
        contents: list = []
        for image in images:
            if image.label:
                contents.append(image.label)
            contents.append(genai_types.Part.from_bytes(data=image.data, mime_type=image.mime_type))
        contents.append(prompt)
        return contents

    def _create_translation_prompt(self, text: str) -> str:
        return f"{self._translation_instructions()}\n\nTranslate this text:\n\n{text}"

    def _postprocess_text(self, text: str) -> str:
        if self.lang_out == "vi":
            # Strip Gemini's markdown emphasis before delegating to the shared
            # punctuation/quote cleanup. Only asterisks that wrap words: removing
            # every `*` also took footnote markers and `2 * 3` out of the PDF.
            text = _BOLD.sub(r"\1", text)
            text = _ITALIC.sub(r"\1", text)
        return super()._postprocess_text(text)

    def validate_configuration(self) -> tuple[bool, str]:
        """Validate Gemini configuration."""
        try:
            if not self.api_key:
                return False, "API key is missing"
            
            # Test API connection with a minimal request
            response = self.client.models.generate_content(
                model=self.model_name,
                contents="Hello",
                config=genai_types.GenerateContentConfig(
                    max_output_tokens=5,
                    temperature=0,
                ),
            )
            
            if response.text:
                return True, "Configuration is valid"
            else:
                return False, "Invalid API response"
                
        except Exception as e:
            return False, f"Configuration error: {str(e)}"