from __future__ import annotations

from pathlib import Path
from typing import Callable

from experiments.oneshot.prompts import (
    ONESHOT_SYSTEM_PROMPT,
    _build_oneshot_user_prompt,
)
from src.evaluation.output_layout import write_document, write_model_spec
from src.llm import LLMResponse


def generate_model(
    *,
    document_text: str,
    output_dir: Path,
    llm_caller: Callable[[str, str], LLMResponse],
    label: str = "oneshot",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_document(output_dir, document_text)
    user_prompt = _build_oneshot_user_prompt(document_text)
    prompt = f"{ONESHOT_SYSTEM_PROMPT}\n\n{user_prompt}"
    response = llm_caller(label, prompt)
    spthy_text = (response.content if response is not None else "") or ""
    return write_model_spec(output_dir, spthy_text)


__all__ = ["generate_model"]
