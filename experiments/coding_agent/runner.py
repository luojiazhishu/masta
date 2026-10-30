from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path

from experiments.coding_agent.cli_invoker import AgentTool, ProviderInject, invoke
from experiments.coding_agent.instructions import build_base_brief
from experiments.coding_agent.workspace import prepare_workspace
from src.evaluation.output_layout import MODEL_SPEC_FILENAME

WORK_DIRNAME = ".work"


def generate_model(
    *,
    document_text: str,
    output_dir: Path,
    tool: AgentTool,
    skills_source: Path,
    agent_timeout_seconds: int,
    extra_args: Sequence[str] = (),
    extra_env: Mapping[str, str] | None = None,
    label: str = "coding_agent",
    provider_inject: ProviderInject | None = None,
) -> Path:
    """Drive a coding-agent CLI to convert one document into model.spthy.

    The CLI runs inside ``output_dir/.work/``: that directory hosts the
    document copy, the skills tree, the per-tool config files (when
    provider-bound), and all stdio logs. After the CLI exits we copy the
    generated ``model.spthy`` from the work dir up to ``output_dir`` so
    the result directory contains nothing but artifacts.

    When ``provider_inject`` is ``None`` the host CLI uses its own
    globally-configured account and default model. When supplied, the
    workspace is materialized with a per-tool config that points at the
    given provider, and the matching credential env vars are pushed into
    the CLI subprocess.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir / WORK_DIRNAME
    work_dir.mkdir(parents=True, exist_ok=True)
    prepare_workspace(
        work_dir=work_dir,
        document_text=document_text,
        skills_source=skills_source,
        instructions=build_base_brief(label),
        tool=tool,
        provider_inject=provider_inject,
    )
    invoke(
        tool=tool,
        work_dir=work_dir,
        timeout_seconds=agent_timeout_seconds,
        extra_args=list(extra_args),
        extra_env=dict(extra_env or {}),
        provider_inject=provider_inject,
    )
    work_model = work_dir / MODEL_SPEC_FILENAME
    final_model = output_dir / MODEL_SPEC_FILENAME
    if work_model.is_file():
        shutil.copyfile(work_model, final_model)
    return final_model


__all__ = ["generate_model"]
