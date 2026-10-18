"""
OpenAI translator implementation with Vietnamese optimization.
"""

import logging
from typing import Any, Dict, FrozenSet, List, Optional, Sequence

from openai import OpenAI

from ..providers.registry import provider
from .base import BaseTranslator, OnText, PageImage, TranslationCancelled, deliver_stream, generate_timeout_s
from .translation_cache import llm_cache_get as _llm_cache_get, llm_cache_set as _llm_cache_set


logger = logging.getLogger(__name__)


class OpenAITranslator(BaseTranslator):
    """
    OpenAI-based translator with Vietnamese language optimization.

    Supports GPT-3.5, GPT-4, and other OpenAI models with special handling
    for Vietnamese language translations.
    """

    _SERVICE_NAME = "openai"

    # What an OpenAI-compatible endpoint may refuse for a particular model:
    # reasoning models take no `temperature`, and want `max_completion_tokens`
    # in place of `max_tokens`. See `BaseTranslator._call_adapting`.
    _ADAPTABLE_PARAMS = ("temperature", "max_tokens")

    def __init__(self, lang_in: str, lang_out: str, **kwargs):
        super().__init__(lang_in, lang_out, **kwargs)

    def _setup_translator(self, **kwargs):
        self.api_key = kwargs.get("api_key")
        if not self.api_key:
            raise ValueError("OpenAI API key is required")

        self.model = kwargs.get("model") or provider("openai").default_model
        self.temperature = kwargs.get("temperature", 0.3)
        self.max_tokens = kwargs.get("max_tokens", 4000)
        self.base_url = kwargs.get("base_url")

        # max_retries=0: the SDK's own retry loop bypasses _call_with_backoff's
        # rate limiter, so its attempts don't count against the shared budget.
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url, max_retries=0)

        logger.info(f"OpenAI translator configured with model: {self.model}")

    @staticmethod
    def _sampling_kwargs(
        rejected: FrozenSet[str],
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> Dict[str, Any]:
        """The temperature and token cap, shaped to what this model takes."""
        kwargs: Dict[str, Any] = {}
        if temperature is not None and "temperature" not in rejected:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            name = "max_completion_tokens" if "max_tokens" in rejected else "max_tokens"
            kwargs[name] = max_tokens
        return kwargs
    
    def translate(self, text: str, **kwargs) -> str:
        self._note_translate_call()
        if self.is_cancelled():
            return text

        try:
            processed_text = self._preprocess_text(text)
            if not processed_text.strip():
                return text

            cached = _llm_cache_get(
                processed_text, self.lang_in, self.lang_out, self._SERVICE_NAME, self.model
            )
            if cached is not None:
                self._fire_paragraph_callback(processed_text, cached)
                return cached

            messages = self._create_translation_prompt(processed_text)
            response = self._call_adapting(
                lambda rejected: self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    timeout=30,
                    **self._sampling_kwargs(
                        rejected, self.temperature, self.max_tokens
                    ),
                ),
                self._ADAPTABLE_PARAMS,
            )
            if getattr(response.choices[0], "finish_reason", None) == "length":
                # A reasoning model's reasoning counts toward the token cap, so
                # the reply can end mid-sentence; taken as whole, it would be
                # cached and printed into the PDF.
                return self._handle_translation_error(
                    RuntimeError("The model stopped at its token limit"), text
                )
            content = response.choices[0].message.content
            translated_text = (content or "").strip()
            if not translated_text:
                # Already counted by the outer `except` (a None content raises
                # AttributeError), but named explicitly so the log says what
                # happened instead of "NoneType has no attribute 'strip'".
                return self._handle_translation_error(
                    RuntimeError("OpenAI returned an empty translation"), text
                )
            result = self._postprocess_text(translated_text)
            # The provider, not "openai": OpenRouter and DeepSeek run this
            # class too, and a model name alone doesn't say whose output a
            # paragraph is — the same name behind two cards would serve each
            # other's text.
            _llm_cache_set(
                processed_text, result, self.lang_in, self.lang_out, self._SERVICE_NAME, self.model
            )
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

        Raises on failure, after `_call_with_backoff`'s retries: the chain turns
        the error into the chat's `error` event. It used to be logged and
        swallowed, so the chat answered with a template instead (#31).
        """
        messages: List[Dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": self._user_content(prompt, images)})
        timeout = generate_timeout_s(prompt, system, images)
        if on_text is not None:
            return self._generate_streaming(messages, max_tokens, timeout, on_text)
        response = self._call_adapting(
            lambda rejected: self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                timeout=timeout,
                **self._sampling_kwargs(rejected, 0.3, max_tokens),
            ),
            self._ADAPTABLE_PARAMS,
        )
        if getattr(response.choices[0], "finish_reason", None) == "length":
            # Raised, so the chat shows an error rather than a cut-off answer.
            raise RuntimeError("The model stopped at its token limit")
        content = response.choices[0].message.content
        return content.strip() if content else None

    def _generate_streaming(self, messages: List[Dict[str, Any]], max_tokens: int,
                            timeout: float, on_text: OnText) -> Optional[str]:
        def send(rejected: FrozenSet[str]):
            # Sent and read to its end inside one retried request: a stream
            # that drops midway is asked for again from the start.
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                timeout=timeout,
                stream=True,
                **self._sampling_kwargs(rejected, 0.3, max_tokens),
            )
            finish: List[Optional[str]] = [None]

            def pieces():
                for chunk in stream:
                    # The usage chunk some servers send last has no choices.
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    finish[0] = getattr(choice, "finish_reason", None) or finish[0]
                    yield getattr(getattr(choice, "delta", None), "content", None)

            return deliver_stream(pieces(), on_text), finish[0]

        text, finish = self._call_adapting(send, self._ADAPTABLE_PARAMS)
        if finish == "length":
            raise RuntimeError("The model stopped at its token limit")
        return text.strip() or None

    @staticmethod
    def _user_content(prompt: str, images: Optional[Sequence[PageImage]]) -> Any:
        """The prompt alone as a string, exactly as before images existed, or
        each image after its label and the prompt last, as content parts."""
        if not images:
            return prompt
        parts: List[Dict[str, Any]] = []
        for image in images:
            if image.label:
                parts.append({"type": "text", "text": image.label})
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{image.mime_type};base64,{image.base64()}"},
            })
        parts.append({"type": "text", "text": prompt})
        return parts

    def _create_translation_prompt(self, text: str) -> List[Dict[str, str]]:
        return [
            {"role": "system", "content": self._translation_instructions()},
            {"role": "user", "content": f"Translate this text:\n\n{text}"},
        ]

    def validate_configuration(self) -> tuple[bool, str]:
        """Validate OpenAI configuration."""
        try:
            if not self.api_key:
                return False, "API key is missing"
            
            # A minimal request, shaped like a translation's: a model that
            # refuses what `translate` sends must not come back as valid.
            response = self._call_adapting(
                lambda rejected: self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": "Hello"}],
                    timeout=10,
                    **self._sampling_kwargs(rejected, self.temperature, 16),
                ),
                self._ADAPTABLE_PARAMS,
                backoff=False,
            )
            
            if response.choices:
                return True, "Configuration is valid"
            else:
                return False, "Invalid API response"
                
        except Exception as e:
            return False, f"Configuration error: {str(e)}"