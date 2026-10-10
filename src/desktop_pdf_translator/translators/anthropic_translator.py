"""
Anthropic (Claude) translator implementation with Vietnamese optimization.

BabelDOC has no built-in Anthropic translator — its bundled `translator/translator.py`
only ships an OpenAITranslator. BabelDOC's pipeline however accepts any object that
implements its BaseTranslator protocol (translate(text), lang_in/lang_out, formula
placeholders), so this class plugs in the same way OpenAITranslator/GeminiTranslator do.
"""

import logging
from typing import Any, Dict, FrozenSet, Optional

import anthropic

from ..providers.registry import provider
from .base import BaseTranslator, TranslationCancelled
from .translation_cache import llm_cache_get as _llm_cache_get, llm_cache_set as _llm_cache_set


logger = logging.getLogger(__name__)


_CUT_SHORT = frozenset({"max_tokens", "refusal", "model_context_window_exceeded"})


class AnthropicTranslator(BaseTranslator):
    """
    Anthropic Claude-based translator with Vietnamese language optimization.

    Uses the official `anthropic` Python SDK (Messages API).
    """

    _SERVICE_NAME = "anthropic"

    # Claude Opus 4.7 and later answer 400 to a non-default `temperature`, and
    # Claude Haiku 4.5 to any `effort`. `max_tokens` is required by the
    # Messages API, so it is never left out. See `BaseTranslator._call_adapting`.
    _ADAPTABLE_PARAMS = ("temperature", "effort")

    def __init__(self, lang_in: str, lang_out: str, **kwargs):
        super().__init__(lang_in, lang_out, **kwargs)

    def _setup_translator(self, **kwargs):
        self.api_key = kwargs.get("api_key")
        if not self.api_key:
            raise ValueError("Anthropic API key is required")

        self.model = kwargs.get("model") or provider("anthropic").default_model
        self.temperature = kwargs.get("temperature", 0.3)
        self.max_tokens = kwargs.get("max_tokens", 4000)
        self.base_url = kwargs.get("base_url")

        # max_retries=0: the SDK's own retry loop bypasses _call_with_backoff's
        # rate limiter, so its attempts don't count against the shared budget.
        client_kwargs = {"api_key": self.api_key, "max_retries": 0}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        self.client = anthropic.Anthropic(**client_kwargs)

        logger.info(f"Anthropic translator configured with model: {self.model}")

    @staticmethod
    def _sampling_kwargs(
        rejected: FrozenSet[str], temperature: Optional[float]
    ) -> Dict[str, Any]:
        """The temperature, unless this model refused one."""
        if temperature is None or "temperature" in rejected:
            return {}
        return {"temperature": temperature}

    @staticmethod
    def _effort_kwargs(rejected: FrozenSet[str]) -> Dict[str, Any]:
        """Low effort for a paragraph, unless this model refused effort.

        A paragraph needs no deep reasoning, and a model that thinks by
        default (Claude Opus 5, Claude Sonnet 5) would otherwise spend its
        default effort on every one of a document's paragraphs. Chat answers
        don't send this: they keep the model's own default.
        """
        if "effort" in rejected:
            return {}
        return {"output_config": {"effort": "low"}}

    @staticmethod
    def _stopped_early(response: Any) -> Optional[str]:
        """The stop reason, when the reply can't be taken as whole.

        Thinking counts toward `max_tokens` on a model that thinks by default,
        so a cap sized for the reply can end it mid-sentence, and a refusal
        can carry part of one; a full context window ends it the same way.
        """
        stop = getattr(response, "stop_reason", None)
        return stop if stop in _CUT_SHORT else None

    def translate(self, text: str, **kwargs) -> str:
        self._note_translate_call()
        if self.is_cancelled():
            return text

        try:
            processed_text = self._preprocess_text(text)
            if not processed_text.strip():
                return text

            cached = _llm_cache_get(processed_text, self.lang_in, self.lang_out, "anthropic", self.model)
            if cached is not None:
                self._fire_paragraph_callback(processed_text, cached)
                return cached

            system_prompt, user_prompt = self._create_translation_prompt(processed_text)

            response = self._call_adapting(
                lambda rejected: self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                    timeout=30,
                    **self._sampling_kwargs(rejected, self.temperature),
                    **self._effort_kwargs(rejected),
                ),
                self._ADAPTABLE_PARAMS,
            )

            stop = self._stopped_early(response)
            if stop:
                # Taken as whole, it would be cached and printed into the PDF.
                return self._handle_translation_error(
                    RuntimeError(f"Anthropic stopped early ({stop})"), text
                )

            translated_text = "".join(
                block.text for block in response.content if getattr(block, "type", None) == "text"
            ).strip()

            if not translated_text:
                # Same as any other failed paragraph — see the funnel in
                # base.py. Returning bare source text here left it uncounted
                # and the run cacheable.
                return self._handle_translation_error(
                    RuntimeError("Anthropic returned an empty translation"),
                    text,
                )

            result = self._postprocess_text(translated_text)
            _llm_cache_set(processed_text, result, self.lang_in, self.lang_out, "anthropic", self.model)
            self._fire_paragraph_callback(processed_text, result)
            return result

        except TranslationCancelled:
            return text
        except Exception as e:
            return self._handle_translation_error(e, text)

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 1000,
    ) -> str | None:
        """Freeform generation used by the RAG chain.

        Raises on failure, after `_call_with_backoff`'s retries; see
        `OpenAITranslator.generate`.
        """
        kwargs = {}
        if system:
            kwargs["system"] = system
        response = self._call_adapting(
            lambda rejected: self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
                timeout=60,
                **kwargs,
                **self._sampling_kwargs(rejected, 0.3),
            ),
            self._ADAPTABLE_PARAMS,
        )
        stop = self._stopped_early(response)
        if stop:
            # Raised, so the chat shows an error rather than a cut-off answer.
            raise RuntimeError(f"Claude stopped early ({stop})")
        text = "".join(
            block.text
            for block in response.content
            if getattr(block, "type", None) == "text"
        ).strip()
        return text or None

    def _create_translation_prompt(self, text: str) -> tuple[str, str]:
        return self._translation_instructions(), f"Translate this text:\n\n{text}"

    def validate_configuration(self) -> tuple[bool, str]:
        try:
            if not self.api_key:
                return False, "API key is missing"

            # Shaped like a translation's request: a model that refuses what
            # `translate` sends must not come back as valid.
            response = self._call_adapting(
                lambda rejected: self.client.messages.create(
                    model=self.model,
                    max_tokens=16,
                    messages=[{"role": "user", "content": "Hello"}],
                    timeout=10,
                    **self._sampling_kwargs(rejected, self.temperature),
                    **self._effort_kwargs(rejected),
                ),
                self._ADAPTABLE_PARAMS,
                backoff=False,
            )

            if response.content:
                return True, "Configuration is valid"
            return False, "Invalid API response"

        except Exception as e:
            return False, f"Configuration error: {str(e)}"
