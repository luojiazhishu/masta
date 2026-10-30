from __future__ import annotations

import random
import re
import time
from typing import Any

from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError

from src.llm.base import LLMClient, LLMError, LLMRateLimitError, LLMResponse


_THINK_BLOCK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)

DEFAULT_MAX_TOKENS = 32768


class OpenAIClient(LLMClient):
    """
    Polymorphic Client for any OpenAI-compatible endpoint.
    """
    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        max_retries: int = 5,
        retry_backoff_seconds: float = 1.0,
        timeout: float = 300.0,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        force_json: bool = True,
        enable_reasoning: bool = False,
    ) -> None:
        self._client = OpenAI(
            api_key=api_key or "sk-no-key-required",
            base_url=base_url,
            timeout=timeout,
        )
        self._model = model
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff_seconds
        self._max_tokens = max_tokens
        self._force_json = force_json
        self._enable_reasoning = enable_reasoning

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float | None = None,
        seed: int | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        messages: list[dict[str, str]] = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        
        if response_schema is not None:
            # Enhanced hint for lighter models
            prompt += "\n\nCRITICAL: You MUST respond with a valid JSON object."
            if "blocks" in str(response_schema):
                prompt += " Root key must be 'blocks'."
            
        messages.append({"role": "user", "content": prompt})

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": self._max_tokens,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if seed is not None:
            kwargs["seed"] = seed

        # --- SMART CONSTRAINTS ---
        if response_schema is not None or self._force_json:
            # Standard JSON Mode (Universal). Always on when force_json
            # so MASTA agents do not need a per-call response_schema to
            # get strict JSON-object output.
            kwargs["response_format"] = {"type": "json_object"}

        extra_body: dict[str, Any] = {}
        if response_schema is not None:
            extra_body["guided_json"] = response_schema
        if self._enable_reasoning:
            # vLLM / Qwen3 / DeepSeek-R1 thinking switch via the chat-template
            # ``enable_thinking`` flag. Server config controls whether the
            # response separates ``reasoning_content`` from ``content``;
            # ``_extract_response`` handles either layout.
            extra_body["chat_template_kwargs"] = {"enable_thinking": True}
        if extra_body:
            kwargs["extra_body"] = extra_body
        # -------------------------

        last_retryable: Exception | None = None
        rate_limited = False

        for attempt in range(self._max_retries):
            try:
                completion = self._client.chat.completions.create(**kwargs)
            except RateLimitError as exc:
                last_retryable = exc
                rate_limited = True
                if attempt + 1 >= self._max_retries:
                    break
                self._sleep_backoff(attempt)
                continue
            except APIConnectionError as exc:
                last_retryable = exc
                rate_limited = False
                if attempt + 1 >= self._max_retries:
                    break
                self._sleep_backoff(attempt)
                continue
            except APIStatusError as exc:
                # Some proxies reject the vLLM/Outlines guided_json extension;
                # downgrade once and retry without applying backoff.
                if "guided_json" in str(exc).lower() and "extra_body" in kwargs:
                    del kwargs["extra_body"]
                    continue
                if (
                    "response_format" in str(exc).lower()
                    and "response_format" in kwargs
                ):
                    del kwargs["response_format"]
                    continue
                if _is_retryable_status(exc):
                    last_retryable = exc
                    rate_limited = False
                    if attempt + 1 >= self._max_retries:
                        break
                    self._sleep_backoff(attempt)
                    continue
                raise LLMError(f"api endpoint error: {exc}") from exc

            return _extract_response(completion)

        assert last_retryable is not None
        if rate_limited:
            raise LLMRateLimitError(
                f"rate limit exceeded after {self._max_retries} attempts: {last_retryable}"
            ) from last_retryable
        raise LLMError(
            f"transient failure after {self._max_retries} attempts: {last_retryable}"
        ) from last_retryable

    def _sleep_backoff(self, attempt: int) -> None:
        delay = self._retry_backoff * (2 ** attempt)
        if delay <= 0:
            return
        jitter = delay * random.random() * 0.5
        time.sleep(delay + jitter)


def _is_retryable_status(exc: APIStatusError) -> bool:
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and status >= 500


def _extract_response(completion: Any) -> LLMResponse:
    choices = getattr(completion, "choices", None) or []
    content = ""
    thinking = ""
    if choices:
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None) or ""
        # vLLM reasoning parsers (DeepSeek-R1, Qwen3, ...) attach the CoT
        # to ``reasoning_content``; OpenAI o-series exposes a similar
        # field. Either way we treat it as the ``thinking`` channel.
        reasoning = getattr(message, "reasoning_content", None)
        if not isinstance(reasoning, str):
            reasoning = getattr(message, "reasoning", None)
        if isinstance(reasoning, str) and reasoning:
            thinking = reasoning
    if not thinking and content:
        # Some endpoints emit the CoT inline as ``<think>...</think>``;
        # peel it out so the JSON parser only sees the action body.
        match = _THINK_BLOCK_RE.search(content)
        if match:
            thinking = match.group(1).strip()
            content = _THINK_BLOCK_RE.sub("", content).strip()
    usage = getattr(completion, "usage", None)
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    return LLMResponse(
        content=content,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        thinking=thinking,
    )
