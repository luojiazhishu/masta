from __future__ import annotations

import time
from typing import Any

from anthropic import Anthropic, APIConnectionError, APIStatusError, RateLimitError

from src.llm.base import LLMClient, LLMError, LLMRateLimitError, LLMResponse

DEFAULT_MAX_TOKENS = 32768  # Generous budget so reasoning-style models do
# not exhaust the budget on internal thinking and return an empty content.


class AnthropicClient(LLMClient):
    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        max_retries: int = 5,
        retry_backoff_seconds: float = 2.0,
        timeout: float = 300.0,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        enable_thinking: bool = False,
        thinking_budget: int | None = None,
    ) -> None:
        self._client = Anthropic(api_key=api_key, timeout=timeout)
        self._model = model
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff_seconds
        self._max_tokens = max_tokens
        self._enable_thinking = enable_thinking
        self._thinking_budget = thinking_budget or 8192

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float | None = None,
        seed: int | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system is not None:
            kwargs["system"] = system
        if self._enable_thinking:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self._thinking_budget,
            }
            if self._max_tokens <= self._thinking_budget:
                kwargs["max_tokens"] = self._thinking_budget + DEFAULT_MAX_TOKENS
        elif temperature is not None:
            kwargs["temperature"] = temperature

        for attempt in range(self._max_retries):
            try:
                message = self._client.messages.create(**kwargs)
            except RateLimitError as exc:
                if attempt + 1 >= self._max_retries:
                    raise LLMRateLimitError(f"anthropic rate limit exceeded: {exc}") from exc
                time.sleep(self._retry_backoff * (attempt + 1))
                continue
            except APIConnectionError as exc:
                # APITimeoutError is a subclass of APIConnectionError, so a
                # request that exceeds ``timeout`` (default 300s) lands here
                # and is retried with the same backoff as a rate-limit hit.
                if attempt + 1 >= self._max_retries:
                    raise LLMError(
                        f"anthropic connection failure after "
                        f"{self._max_retries} attempts: {exc}"
                    ) from exc
                time.sleep(self._retry_backoff * (attempt + 1))
                continue
            except APIStatusError as exc:
                raise LLMError(f"anthropic api error: {exc}") from exc
            return _extract_response(message)

        raise LLMRateLimitError("anthropic rate limit exceeded")


def _extract_response(message: Any) -> LLMResponse:
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        block_type = getattr(block, "type", "")
        if block_type == "thinking":
            think = getattr(block, "thinking", None)
            if isinstance(think, str):
                thinking_parts.append(think)
            continue
        text = getattr(block, "text", None)
        if isinstance(text, str):
            text_parts.append(text)
    usage = getattr(message, "usage", None)
    prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    return LLMResponse(
        content="".join(text_parts),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        thinking="".join(thinking_parts),
    )
