"""Run the Merged ablation on a single document.txt to generate model.spthy."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from experiments.ablations.merged.runner import generate_model
from src.llm import LLMConfig, build_llm_caller, create_llm_client
from src.logging_ import configure_logging
from src.tamarin.runner import TamarinRunner


WORK_DIRNAME = ".work"

load_dotenv()


def main(argv: list[str] | None = None) -> int:
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

    print("==== MASTA merged_model ====")
    print(f" document:   {document_path}")
    print(f" output_dir: {output_dir}")
    print(f" work_dir:   {work_dir}")
    print(f" provider:   {llm_config.provider} / {llm_config.model}")

    document_text = document_path.read_text(encoding="utf-8")
    final_model = generate_model(
        document_text=document_text,
        output_dir=output_dir,
        llm_caller=llm_caller,
        runner=runner,
    )

    if not final_model.exists():
        print("ERROR: merged run ended without producing model.spthy", file=sys.stderr)
        return 1

    print(f"[merged] model written to: {final_model}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the Merged ablation on one document.txt to produce model.spthy"
    )
    parser.add_argument("--document", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--provider", required=True, choices=["openai", "anthropic"])
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--tamarin-timeout", type=int, default=300)
    parser.add_argument(
        "--reasoning", action=argparse.BooleanOptionalAction, default=False
    )
    parser.add_argument("--reasoning-budget", type=int, default=None)
    parser.add_argument(
        "--cache", action=argparse.BooleanOptionalAction, default=True
    )
    return parser


if __name__ == "__main__":
    sys.exit(main())
