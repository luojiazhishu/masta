"""Run a coding-agent CLI on a single document.txt to generate model.spthy.

This script only produces the formal model. Scoring against a dataset
reference is the responsibility of ``scripts/score_model.py``.

Two modes are supported:

* default — pass ``--model <annotation>`` only; the host CLI uses its
  own globally-configured account and default model.

* provider-bound — also pass ``--provider``, ``--api-model`` and
  ``--api-key`` (plus ``--base-url`` when applicable). The workspace
  receives a per-tool config pointing at the provider, and the
  matching credential env vars are pushed into the CLI subprocess.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from experiments.coding_agent import generate_model
from experiments.coding_agent.cli_invoker import ProviderInject


load_dotenv()


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    document_path = args.document.resolve()
    if not document_path.is_file():
        print(f"ERROR: document not found: {document_path}", file=sys.stderr)
        return 2

    skills_source = args.skills_source.resolve()
    if not skills_source.is_dir():
        print(
            f"ERROR: skills source dir not found: {skills_source}",
            file=sys.stderr,
        )
        return 2

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    provider_inject = _build_provider_inject(args)

    print("==== MASTA coding_agent_model ====")
    print(f" document:   {document_path}")
    print(f" output_dir: {output_dir}")
    print(f" tool:       {args.tool}")
    if provider_inject is not None:
        print(
            f" provider:   {provider_inject.provider} / {provider_inject.model}"
            f" @ {provider_inject.base_url or '(default base)'}"
        )
    elif args.model:
        print(f" model tag:  {args.model} (host CLI default account)")
    print(f" label:      {args.label}")

    document_text = document_path.read_text(encoding="utf-8")
    model_path = generate_model(
        document_text=document_text,
        output_dir=output_dir,
        tool=args.tool,
        skills_source=skills_source,
        agent_timeout_seconds=args.agent_timeout,
        extra_args=tuple(args.extra_arg or ()),
        extra_env=_parse_extra_env(args.extra_env),
        label=args.label,
        provider_inject=provider_inject,
    )

    if not model_path.exists() or not model_path.read_text(encoding="utf-8").strip():
        print(
            f"ERROR: coding agent produced empty {model_path.name}",
            file=sys.stderr,
        )
        return 1

    print(f"[coding_agent] model written to: {model_path}")
    return 0


def _build_provider_inject(args: argparse.Namespace) -> ProviderInject | None:
    fields = (args.provider, args.api_model, args.api_key)
    if all(v is None or v == "" for v in fields):
        return None
    missing = [
        name
        for name, value in (
            ("--provider", args.provider),
            ("--api-model", args.api_model),
            ("--api-key", args.api_key),
        )
        if not value
    ]
    if missing:
        raise SystemExit(
            f"provider-bound mode requires all of --provider/--api-model/"
            f"--api-key; missing: {missing}"
        )
    return ProviderInject(
        provider=args.provider,
        model=args.api_model,
        api_key=args.api_key,
        base_url=args.base_url or "",
    )


def _parse_extra_env(items: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or ():
        key, sep, value = item.partition("=")
        if not sep or not key:
            continue
        out[key] = value
    return out


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a coding-agent CLI on one document.txt to produce model.spthy"
    )
    parser.add_argument(
        "--document",
        type=Path,
        required=True,
        help="Path to the input document.txt",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory to receive model.spthy plus the agent's workspace",
    )
    parser.add_argument(
        "--tool",
        required=True,
        choices=["codex", "opencode", "claude_code"],
        help="Coding-agent CLI to invoke",
    )
    parser.add_argument(
        "--model",
        default="",
        help=(
            "Annotation only — printed to stdout for traceability. Not "
            "pushed into the CLI; in provider-bound mode the real model "
            "name is taken from --api-model"
        ),
    )
    parser.add_argument(
        "--provider",
        default=None,
        choices=["openai", "anthropic"],
        help="Provider kind (provider-bound mode)",
    )
    parser.add_argument(
        "--api-model",
        default=None,
        help="Real model id sent to the API (provider-bound mode)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Provider API key (provider-bound mode)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Provider base URL (provider-bound mode; optional for anthropic)",
    )
    parser.add_argument(
        "--skills-source",
        type=Path,
        default=Path("coding_agent/skills"),
        help="Source directory of the skills tree to copy into the workspace",
    )
    parser.add_argument(
        "--agent-timeout",
        type=int,
        default=1800,
        help="Per-CLI-invocation timeout in seconds",
    )
    parser.add_argument(
        "--extra-arg",
        action="append",
        default=[],
        help="Extra argument forwarded verbatim to the CLI argv (repeat for multiple)",
    )
    parser.add_argument(
        "--extra-env",
        action="append",
        default=[],
        help="Extra environment variable as KEY=VALUE (repeat for multiple)",
    )
    parser.add_argument(
        "--label",
        default="coding_agent",
        help="Free-form tag stamped into the agent task brief",
    )
    return parser


if __name__ == "__main__":
    sys.exit(main())
