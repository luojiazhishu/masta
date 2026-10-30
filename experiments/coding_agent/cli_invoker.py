"""Headless CLI launch for opencode / codex / claude code.

Each tool can run in one of two modes:

* **default** — no provider info is supplied. The CLI runs against its
  own globally-configured account and default model; we do not inject
  any per-workspace config or env.

* **provider-bound** — a :class:`ProviderInject` is supplied. We point
  the CLI at a per-workspace config file (written by :mod:`workspace`)
  and inject the matching ``OPENAI_API_KEY`` / ``ANTHROPIC_*`` env vars
  so the CLI hits the OpenAI-compatible / Anthropic endpoint described
  by ``providers.<id>`` in ``experiment.yaml``.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


AgentTool = Literal["opencode", "codex", "claude_code"]


LAUNCH_PROMPT = (
    "Read the task brief in this directory (AGENTS.md or CLAUDE.md) and "
    "execute the task it describes. Stop only after model.spthy exists in "
    "this directory."
)


@dataclass(frozen=True)
class ProviderInject:
    """Provider credentials to push into the host CLI for a single run."""

    provider: str        # "openai" | "anthropic"
    model: str           # real model id sent to the API
    api_key: str
    base_url: str = ""


@dataclass
class AgentInvocationResult:
    returncode: int
    duration_seconds: float
    stdout_path: Path
    stderr_path: Path


def invoke(
    *,
    tool: AgentTool,
    work_dir: Path,
    timeout_seconds: int,
    extra_args: list[str],
    extra_env: dict[str, str],
    provider_inject: ProviderInject | None = None,
) -> AgentInvocationResult:
    work_dir = work_dir.resolve()
    log_dir = work_dir / ".agent"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / "stdout.log"
    stderr_path = log_dir / "stderr.log"

    argv, env_addition, env_unset, prompt_input = _launch_spec(
        tool, work_dir, list(extra_args), provider_inject
    )

    env = os.environ.copy()
    for key in env_unset:
        env.pop(key, None)
    env.update(env_addition)
    env.update(extra_env)

    start = time.monotonic()
    with stdout_path.open("w", encoding="utf-8") as out, stderr_path.open(
        "w", encoding="utf-8"
    ) as err:
        try:
            completed = subprocess.run(
                argv,
                cwd=str(work_dir),
                env=env,
                input=prompt_input,
                stdout=out,
                stderr=err,
                text=True,
                timeout=timeout_seconds,
            )
            returncode = completed.returncode
        except subprocess.TimeoutExpired:
            err.write(f"\n[runner] agent timed out after {timeout_seconds}s\n")
            returncode = -1
        except FileNotFoundError as exc:
            err.write(f"\n[runner] {exc}\n")
            returncode = -2
    duration = time.monotonic() - start

    return AgentInvocationResult(
        returncode=returncode,
        duration_seconds=duration,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )


# Spec return contract: (argv, env_set, env_unset, prompt_input).
# `env_unset` lists keys to drop from the inherited os.environ before
# `env_set` is applied — used to strip nested-CLI markers that would
# otherwise force the launched CLI into "running inside another agent"
# mode and skip credential loading.

# When the runner itself was launched from a Claude Code embedded
# terminal, the parent shell carries these variables. Inheriting them
# into a fresh `claude --print` subprocess makes the new CLI think it
# is already running under another agent and refuse to load
# ~/.claude/.credentials.json (apiKeySource: "none"). Strip them so
# the worker behaves exactly like a clean shell launch.
_CLAUDE_NESTED_ENV_KEYS: tuple[str, ...] = (
    "CLAUDECODE",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "AI_AGENT",
)


def _launch_spec(
    tool: AgentTool,
    work_dir: Path,
    extra_args: list[str],
    provider_inject: ProviderInject | None,
) -> tuple[list[str], dict[str, str], list[str], str]:
    if tool == "codex":
        return _spec_codex(work_dir, extra_args, provider_inject)
    if tool == "claude_code":
        return _spec_claude_code(work_dir, extra_args, provider_inject)
    if tool == "opencode":
        return _spec_opencode(work_dir, extra_args, provider_inject)
    raise ValueError(f"unknown tool: {tool}")


def _spec_codex(
    work_dir: Path,
    extra_args: list[str],
    provider_inject: ProviderInject | None,
) -> tuple[list[str], dict[str, str], list[str], str]:
    argv: list[str] = [
        "codex",
        "exec",
        "--cd",
        str(work_dir),
        "--skip-git-repo-check",
        "--dangerously-bypass-approvals-and-sandbox",
        "-o",
        str(work_dir / ".agent" / "last_message.txt"),
        *extra_args,
        "-",
    ]
    env: dict[str, str] = {}
    if provider_inject is not None:
        env["CODEX_HOME"] = str(work_dir / ".codex")
        env["OPENAI_API_KEY"] = provider_inject.api_key
    return argv, env, [], LAUNCH_PROMPT


def _spec_claude_code(
    work_dir: Path,
    extra_args: list[str],
    provider_inject: ProviderInject | None,
) -> tuple[list[str], dict[str, str], list[str], str]:
    # NOTE: do NOT pass `--bare` here. claude code's --bare flag skips
    # the persistent session / credential bootstrap path, so a subsequent
    # API call returns "Not logged in" even when ~/.claude/.credentials.json
    # is present. Reproduced empirically: with --bare the result row is
    # is_error=true / duration_api_ms=0 / "Not logged in"; without --bare
    # the same cmdline returns the real model response.
    argv: list[str] = [
        "claude",
        "--print",
        "--add-dir",
        str(work_dir),
    ]
    if provider_inject is not None:
        argv += ["--settings", str(work_dir / ".claude" / "settings.json")]
    argv += [
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        "Bash",
        "Edit",
        "Write",
        "Read",
        "Glob",
        "Grep",
        "MultiEdit",
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        "--verbose",
        *extra_args,
    ]
    env: dict[str, str] = {}
    if provider_inject is not None:
        if provider_inject.base_url:
            env["ANTHROPIC_BASE_URL"] = provider_inject.base_url
        env["ANTHROPIC_AUTH_TOKEN"] = provider_inject.api_key
        env["ANTHROPIC_API_KEY"] = provider_inject.api_key
    return argv, env, list(_CLAUDE_NESTED_ENV_KEYS), LAUNCH_PROMPT


def _spec_opencode(
    work_dir: Path,
    extra_args: list[str],
    provider_inject: ProviderInject | None,
) -> tuple[list[str], dict[str, str], list[str], str]:
    argv: list[str] = [
        "opencode",
        "run",
        "--dir",
        str(work_dir),
        "--dangerously-skip-permissions",
        *extra_args,
        LAUNCH_PROMPT,
    ]
    # opencode persists a single SQLite database under
    # ``$XDG_DATA_HOME/opencode/opencode.db`` (default
    # ``~/.local/share/opencode/``) and is not concurrent-safe: when
    # several workers launch in the same instant they all race to do
    # ``PRAGMA journal_mode = WAL`` on the same file and one of them
    # fails with "Failed to run the query 'PRAGMA journal_mode = WAL'".
    # Give every worker its own data + cache root so each opencode
    # instance owns a private DB / log / snapshot tree. XDG_CONFIG_HOME
    # is intentionally NOT redirected so the user's globally-configured
    # providers under ``~/.config/opencode/`` remain discoverable.
    env: dict[str, str] = {
        "XDG_DATA_HOME": str(work_dir / ".opencode-data"),
        "XDG_CACHE_HOME": str(work_dir / ".opencode-cache"),
    }
    if provider_inject is not None:
        env["OPENAI_API_KEY"] = provider_inject.api_key
    return argv, env, [], ""


__all__ = ["AgentInvocationResult", "AgentTool", "ProviderInject", "invoke"]
