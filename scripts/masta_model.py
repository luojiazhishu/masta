"""Run the MASTA agent loop on a single document.txt to generate model.spthy.

This script only produces the formal model. Scoring against a dataset
reference is the responsibility of ``scripts/score_model.py``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.llm import LLMConfig, build_llm_caller, create_llm_client
from src.logging_ import configure_logging
from src.orchestration import MastaSystem
from src.tamarin.runner import TamarinRunner


WORK_DIRNAME = ".work"
MODEL_SPEC_FILENAME = "model.spthy"


load_dotenv()


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    args = _build_parser().parse_args(argv)

    document_path = args.document.resolve()
    if not document_path.is_file():
        print(f"ERROR: document not found: {document_path}", file=sys.stderr)
        return 2

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir / WORK_DIRNAME
    work_dir.mkdir(parents=True, exist_ok=True)
    configure_logging(work_dir)

    llm_config = LLMConfig(
        provider=args.provider,
        model=args.model,
        api_key=args.api_key or os.environ.get("MASTA_LLM_API_KEY"),
        base_url=args.base_url or os.environ.get("MASTA_LLM_BASE_URL"),
        max_tokens=args.max_tokens,
        reasoning=args.reasoning,
        reasoning_budget=args.reasoning_budget,
    )
    llm_caller = build_llm_caller(
        create_llm_client(llm_config),
        temperature=args.temperature,
        seed=args.seed,
        use_cache=args.cache,
        cache_path=work_dir / "llm_cache.json",
    )
    runner = TamarinRunner(timeout_seconds=args.tamarin_timeout)

    print("==== MASTA generate_model ====")
    print(f" document:   {document_path}")
    print(f" output_dir: {output_dir}")
    print(f" work_dir:   {work_dir}")
    print(f" provider:   {llm_config.provider} / {llm_config.model}")

    document_text = document_path.read_text(encoding="utf-8")
    end_reason = MastaSystem(
        work_dir=work_dir, runner=runner, llm_caller=llm_caller
    ).run(initial_document=document_text)

    work_model = work_dir / MODEL_SPEC_FILENAME
    if not work_model.exists():
        print(
            f"ERROR: agent loop ended without producing model.spthy "
            f"(reason={end_reason!r})",
            file=sys.stderr,
        )
        return 1

    final_model = output_dir / MODEL_SPEC_FILENAME
    shutil.copyfile(work_model, final_model)
    print(f"[generate] done; reason={end_reason!r}")
    print(f"[generate] model written to: {final_model}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run MASTA agents on one document.txt to produce model.spthy"
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
        help="Directory to receive model.spthy plus the agent's intermediate files",
    )
    parser.add_argument(
        "--provider", required=True, choices=["openai", "anthropic"]
    )
    parser.add_argument("--model", required=True, help="LLM model id")
    parser.add_argument(
        "--api-key",
        default=None,
        help="LLM API key (falls back to MASTA_LLM_API_KEY)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="LLM base URL (falls back to MASTA_LLM_BASE_URL)",
    )
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--reasoning",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable the model's native reasoning / thinking channel "
             "(Anthropic extended thinking, vLLM reasoning_content, ...). "
             "Default: disabled.",
    )
    parser.add_argument(
        "--reasoning-budget",
        type=int,
        default=None,
        help="Reasoning token budget; only used by providers that accept "
             "an explicit budget (e.g. Anthropic extended thinking).",
    )
    parser.add_argument(
        "--tamarin-timeout",
        type=int,
        default=300,
        help="Per-Tamarin-invocation timeout in seconds",
    )
    parser.add_argument(
        "--cache",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use the on-disk LLM-call cache (default: enabled; "
             "pass --no-cache to disable)",
    )
    return parser


if __name__ == "__main__":
    sys.exit(main())
