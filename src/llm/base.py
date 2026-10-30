from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMResponse:
    content: str
    prompt_tokens: int
    completion_tokens: int
    thinking: str = ""


class LLMError(Exception):
    pass


class LLMRateLimitError(LLMError):
    pass


class LLMClient(ABC):
    @abstractmethod
    def complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float | None = None,
        seed: int | None = None,
    ) -> LLMResponse: ...
