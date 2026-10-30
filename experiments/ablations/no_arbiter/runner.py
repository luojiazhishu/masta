from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

from experiments.ablations.no_arbiter.system import NoArbiterSystem

MODEL_SPEC_FILENAME = "model.spthy"
WORK_DIRNAME = ".work"


def generate_model(
    *,
    document_text: str,
    output_dir: Path,
    llm_caller: Callable[[str, str], str],
    runner: Any,
) -> Path:
    """Run NoArbiterSystem and copy model.spthy to output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir / WORK_DIRNAME
    work_dir.mkdir(parents=True, exist_ok=True)
    system = NoArbiterSystem(
        work_dir=work_dir, runner=runner, llm_caller=llm_caller
    )
    system.run(initial_document=document_text)
    work_model = work_dir / MODEL_SPEC_FILENAME
    final_model = output_dir / MODEL_SPEC_FILENAME
    if work_model.is_file():
        shutil.copyfile(work_model, final_model)
    return final_model


__all__ = ["generate_model"]
