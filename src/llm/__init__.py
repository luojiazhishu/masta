from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.llm.anthropic_client import AnthropicClient
from src.llm.base import LLMClient, LLMError, LLMRateLimitError, LLMResponse
from src.llm.openai_client import OpenAIClient
from src.logging_ import EventLogger


_ENV_PROVIDER = "MASTA_LLM_PROVIDER"
_ENV_MODEL = "MASTA_LLM_MODEL"
_ENV_BASE_URL = "MASTA_LLM_BASE_URL"
_ENV_API_KEY = "MASTA_LLM_API_KEY"
_ENV_REASONING = "MASTA_LLM_REASONING"
_ENV_REASONING_BUDGET = "MASTA_LLM_REASONING_BUDGET"

_DEFAULT_CACHE_FILE = Path.home() / ".cache" / "masta" / "llm_cache.json"


def _cache_load(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _cache_save(cache: dict[str, dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(cache, ensure_ascii=False), encoding="utf-8"
    )
    tmp.replace(path)


def _cache_key(
    provider: str,
    model: str,
    prompt: str,
    temperature: float | None,
    seed: int | None,
) -> str:
    payload = json.dumps(
        [provider, model, prompt, temperature, seed],
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LLMConfigError(ValueError):
    """Raised when MASTA_LLM_* environment variables are missing or invalid."""


@dataclass(frozen=True)
class LLMConfig:
    """Provider-agnostic settings consumed by ``create_llm_client``.

    ``provider`` selects the SDK wrapper; ``model`` / ``api_key`` /
    ``base_url`` are forwarded to it. ``max_tokens`` is optional and
    overrides the wrapper's default budget when provided. The dataclass
    has no I/O of its own — callers either build it from the environment
    via :func:`load_llm_config_from_env` or construct it directly (e.g.
    in tests).
    """

    provider: str
    model: str
    api_key: str | None = None
    base_url: str | None = None
    max_tokens: int | None = None
    reasoning: bool = False
    reasoning_budget: int | None = None


def load_llm_config_from_env(
    *,
    model_override: str | None = None,
    max_tokens: int | None = None,
) -> LLMConfig:
    """Build an :class:`LLMConfig` from ``MASTA_LLM_*`` environment vars.

    ``MASTA_LLM_PROVIDER`` and ``MASTA_LLM_MODEL`` are required (the model
    can be overridden via ``model_override`` for runs that pin a
    different model from the YAML config). For the openai provider
    ``MASTA_LLM_BASE_URL`` and ``MASTA_LLM_API_KEY`` should be set; for
    anthropic only ``MASTA_LLM_API_KEY`` matters. Missing required
    variables raise :class:`LLMConfigError` with a clear message.
    """

    provider = (os.environ.get(_ENV_PROVIDER) or "").strip().lower()
    if provider not in {"openai", "anthropic"}:
        raise LLMConfigError(
            f"{_ENV_PROVIDER} must be 'openai' or 'anthropic', "
            f"got {provider!r}"
        )
    model = model_override or os.environ.get(_ENV_MODEL) or ""
    if not model.strip():
        raise LLMConfigError(
            f"{_ENV_MODEL} must be set (or pass model_override)"
        )
    reasoning_flag = (os.environ.get(_ENV_REASONING) or "").strip().lower()
    reasoning = reasoning_flag in {"1", "true", "yes", "on"}
    reasoning_budget_raw = os.environ.get(_ENV_REASONING_BUDGET)
    reasoning_budget: int | None = None
    if reasoning_budget_raw:
        try:
            reasoning_budget = int(reasoning_budget_raw)
        except ValueError as exc:
            raise LLMConfigError(
                f"{_ENV_REASONING_BUDGET} must be an integer, "
                f"got {reasoning_budget_raw!r}"
            ) from exc
    return LLMConfig(
        provider=provider,
        model=model,
        api_key=os.environ.get(_ENV_API_KEY),
        base_url=os.environ.get(_ENV_BASE_URL),
        max_tokens=max_tokens,
        reasoning=reasoning,
        reasoning_budget=reasoning_budget,
    )


def create_llm_client(config: LLMConfig) -> LLMClient:
    """Instantiate the appropriate client wrapper for the given config."""

    extra_kwargs: dict[str, int] = {}
    if config.max_tokens is not None:
        extra_kwargs["max_tokens"] = config.max_tokens

    if config.provider == "anthropic":
        return AnthropicClient(
            model=config.model,
            api_key=config.api_key,
            enable_thinking=config.reasoning,
            thinking_budget=config.reasoning_budget,
            **extra_kwargs,
        )
    if config.provider == "openai":
        return OpenAIClient(
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
            enable_reasoning=config.reasoning,
            **extra_kwargs,
        )
    raise LLMConfigError(f"Unsupported LLM provider: {config.provider!r}")


def build_llm_caller(
    client: LLMClient,
    *,
    temperature: float | None = None,
    seed: int | None = None,
    use_cache: bool = True,
    cache_path: Path | None = None,
) -> Callable[[str, str], LLMResponse]:
    """Adapt an :class:`LLMClient` to the ``(role, prompt) -> LLMResponse``
    signature the agent runtime expects.

    ``role`` is informational — it is dispatched by the host, not the
    model — so the wrapper passes only the prompt through to
    ``client.complete``. ``temperature`` and ``seed`` are forwarded
    verbatim if provided. Each call prints a one-line summary
    (``[llm] role tokens=in/out wall=Xs``) and emits a structured
    ``llm_call`` event into ``events.jsonl``. The full ``LLMResponse``
    (including any native ``thinking`` channel content from extended
    thinking / reasoning models) is returned to the caller.
    """

    events = EventLogger()
    resolved_cache_path = cache_path if cache_path is not None else _DEFAULT_CACHE_FILE
    cache = _cache_load(resolved_cache_path) if use_cache else {}
    provider = client.__class__.__name__
    model = getattr(client, "_model", "") or ""

    def caller(role: str, prompt: str) -> LLMResponse:
        if use_cache:
            key = _cache_key(provider, model, prompt, temperature, seed)
            cached = cache.get(key)
            if cached is not None:
                events.log_llm_call(
                    agent_role=role,
                    prompt_tokens=int(cached.get("prompt_tokens", 0)),
                    completion_tokens=int(cached.get("completion_tokens", 0)),
                    wall_time_seconds=0.0,
                )
                return LLMResponse(
                    content=cached.get("content", ""),
                    prompt_tokens=int(cached.get("prompt_tokens", 0)),
                    completion_tokens=int(cached.get("completion_tokens", 0)),
                    thinking=cached.get("thinking", "") or "",
                )

        started = time.monotonic()
        response = client.complete(
            prompt=prompt,
            temperature=temperature,
            seed=seed,
        )
        elapsed = time.monotonic() - started
        events.log_llm_call(
            agent_role=role,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            wall_time_seconds=elapsed,
        )
        if use_cache:
            cache[key] = {
                "content": response.content,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "thinking": response.thinking,
            }
            _cache_save(cache, resolved_cache_path)
        return response

    return caller


__all__ = [
    "AnthropicClient",
    "LLMClient",
    "LLMConfig",
    "LLMConfigError",
    "LLMError",
    "LLMRateLimitError",
    "LLMResponse",
    "OpenAIClient",
    "build_llm_caller",
    "create_llm_client",
    "load_llm_config_from_env",
]
