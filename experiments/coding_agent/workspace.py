"""Per-protocol working-directory preparation.

Each task (base protocol, action variant, weakness variant) runs in its
own self-contained directory. Only the entry-point file the configured
tool actually auto-discovers is materialized — codex and opencode read
``AGENTS.md`` (the cross-CLI open standard), claude code reads
``CLAUDE.md``. We do NOT drop both files into every workspace.

In **default** mode no per-tool config file is written; the host CLI
uses its own globally-configured account and default model.

In **provider-bound** mode (``provider_inject`` is supplied) we write
``.codex/config.toml`` / ``opencode.json`` / ``.claude/settings.json``
with the model id and base URL from the inject. The API key itself
flows only through env vars exported by :mod:`cli_invoker`.

Layout (per task)::

    <work_dir>/
        document.txt              -- protocol/variant description (the only input)
        skills/                   -- copied from coding_agent/skills/
        AGENTS.md   (codex / opencode)
        CLAUDE.md   (claude_code)
        .codex/config.toml        -- written only in provider-bound mode (codex)
        opencode.json             -- written only in provider-bound mode (opencode)
        .claude/settings.json     -- written only in provider-bound mode (claude_code)
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from experiments.coding_agent.cli_invoker import AgentTool, ProviderInject

AGENTS_FILENAME = "AGENTS.md"
CLAUDE_FILENAME = "CLAUDE.md"
DOCUMENT_FILENAME = "document.txt"
SKILLS_DIRNAME = "skills"
TAMARIN_MANUAL_DIRNAME = "tamarin_manual"
DEFAULT_TAMARIN_MANUAL_SOURCE = (
    Path(__file__).resolve().parents[2] / "data" / "tamarin"
)

_INSTRUCTION_FILENAME: dict[AgentTool, str] = {
    "codex": AGENTS_FILENAME,
    "opencode": AGENTS_FILENAME,
    "claude_code": CLAUDE_FILENAME,
}


def prepare_workspace(
    *,
    work_dir: Path,
    document_text: str,
    skills_source: Path,
    instructions: str,
    tool: AgentTool,
    provider_inject: ProviderInject | None = None,
    tamarin_manual_source: Path | None = None,
) -> None:
    """Materialize every file the coding-agent CLI will read on launch."""

    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / DOCUMENT_FILENAME).write_text(document_text, encoding="utf-8")

    skills_dest = work_dir / SKILLS_DIRNAME
    if skills_dest.exists():
        shutil.rmtree(skills_dest)
    shutil.copytree(skills_source, skills_dest)

    manual_source = (
        tamarin_manual_source
        if tamarin_manual_source is not None
        else DEFAULT_TAMARIN_MANUAL_SOURCE
    )
    if manual_source.is_dir():
        manual_dest = work_dir / TAMARIN_MANUAL_DIRNAME
        if manual_dest.exists():
            shutil.rmtree(manual_dest)
        shutil.copytree(manual_source, manual_dest)

    instruction_filename = _INSTRUCTION_FILENAME.get(tool)
    if instruction_filename is None:
        raise ValueError(f"unknown tool: {tool}")
    (work_dir / instruction_filename).write_text(instructions, encoding="utf-8")

    if provider_inject is None:
        return
    if tool == "codex":
        _write_codex_config(work_dir, provider_inject)
    elif tool == "opencode":
        _write_opencode_config(work_dir, provider_inject)
    elif tool == "claude_code":
        _write_claude_settings(work_dir, provider_inject)


def _write_codex_config(work_dir: Path, inject: ProviderInject) -> None:
    home = work_dir / ".codex"
    home.mkdir(parents=True, exist_ok=True)
    config = (
        f'model = "{inject.model}"\n'
        'model_provider = "masta"\n'
        'approval_policy = "never"\n'
        'sandbox_mode = "danger-full-access"\n'
        '\n'
        '[model_providers.masta]\n'
        'name = "MASTA"\n'
        f'base_url = "{inject.base_url}"\n'
        'env_key = "OPENAI_API_KEY"\n'
        'wire_api = "responses"\n'
    )
    (home / "config.toml").write_text(config, encoding="utf-8")


def _write_opencode_config(work_dir: Path, inject: ProviderInject) -> None:
    payload = {
        "$schema": "https://opencode.ai/config.json",
        "model": f"masta/{inject.model}",
        "provider": {
            "masta": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "MASTA",
                "options": {
                    "baseURL": inject.base_url,
                    "apiKey": "{env:OPENAI_API_KEY}",
                },
                "models": {inject.model: {"name": inject.model}},
            }
        },
    }
    (work_dir / "opencode.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


def _write_claude_settings(work_dir: Path, inject: ProviderInject) -> None:
    home = work_dir / ".claude"
    home.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": inject.model,
        "permissions": {
            "defaultMode": "acceptEdits",
            "allow": [
                "Bash",
                "Edit",
                "Write",
                "Read",
                "Glob",
                "Grep",
                "MultiEdit",
            ],
        },
    }
    (home / "settings.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


__all__ = [
    "AGENTS_FILENAME",
    "CLAUDE_FILENAME",
    "DEFAULT_TAMARIN_MANUAL_SOURCE",
    "DOCUMENT_FILENAME",
    "SKILLS_DIRNAME",
    "TAMARIN_MANUAL_DIRNAME",
    "prepare_workspace",
]
