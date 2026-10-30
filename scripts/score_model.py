"""Score a generated model.spthy against a dataset's reference protocol entry.

The scorer re-runs Tamarin on the supplied model.spthy to produce the
M1 action-blocks YAML and the per-safety-lemma verdicts, then computes
M1, M2 and M3 against the dataset's baseline. Action and weakness
variants are out of scope: each variant is a separate document and must
be generated and scored on its own; with no variant inputs M4 and M5
collapse to their missing-input semantics (1.0 for vacuous baselines,
0.0 otherwise).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from src.evaluation.dataset_loader import load_dataset
from src.evaluation.protocol_score import score_protocol
from src.model.protocol_prediction import ProtocolPrediction
from src.model.verdict import TamarinVerdict
from src.tamarin.runner import (
    TamarinRunner,
    extract_lemma_definition,
    extract_m1_action_blocks,
    extract_safety_lemma_names,
)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    model_path = args.model.resolve()
    if not model_path.is_file():
        print(f"ERROR: model.spthy not found: {model_path}", file=sys.stderr)
        return 2
    work_dir = model_path.parent

    dataset_dir = args.dataset_dir.resolve()
    if not (dataset_dir / "metadata.yaml").is_file():
        print(
            f"ERROR: metadata.yaml not found under {dataset_dir}",
            file=sys.stderr,
        )
        return 2

    specs = load_dataset(dataset_dir)
    spec = next((s for s in specs if s.id == args.protocol_id), None)
    if spec is None:
        print(
            f"ERROR: protocol id {args.protocol_id!r} not declared in dataset",
            file=sys.stderr,
        )
        return 2

    print("==== MASTA score_model ====")
    print(f" model:       {model_path}")
    print(f" dataset_dir: {dataset_dir}")
    print(f" protocol:    {spec.id}")

    runner = TamarinRunner(timeout_seconds=args.tamarin_timeout)

    yaml_path = extract_m1_action_blocks(
        runner=runner, work_dir=work_dir, output_dir=work_dir
    )
    base_action_blocks_text = (
        yaml_path.read_text(encoding="utf-8")
        if yaml_path is not None and yaml_path.exists()
        else ""
    )

    spec_text = model_path.read_text(encoding="utf-8")
    safety_names = list(extract_safety_lemma_names(spec_text))
    base_lemma_definitions = {
        name: (extract_lemma_definition(spec_text, name) or "")
        for name in safety_names
    }
    base_lemma_verdicts: dict[str, TamarinVerdict | None] = {}
    base_lemma_steps: dict[str, int | None] = {}
    for name in safety_names:
        result = runner.verify_lemma(spec_text, name)
        verdict = getattr(result, "verdict", None)
        if not isinstance(verdict, TamarinVerdict):
            verdict = TamarinVerdict.FALSIFIED
        base_lemma_verdicts[name] = verdict
        base_lemma_steps[name] = getattr(result, "steps", None)

    prediction = ProtocolPrediction(
        protocol_id=spec.id,
        base_action_blocks_text=base_action_blocks_text,
        base_lemma_definitions=base_lemma_definitions,
        base_lemma_verdicts=base_lemma_verdicts,
        base_lemma_steps=base_lemma_steps,
    )
    score = score_protocol(spec, prediction)

    print()
    print("==== Score ====")
    print(f" protocol: {spec.id}")
    print(f" M1: {score.m1:.3f}")
    print(f" M2: {score.m2:.3f}")
    print(f" M3: {score.m3:.3f}")
    print(f" M4: {score.m4:.3f}  (no action variants supplied)")
    print(f" M5: {score.m5:.3f}  (no weakness variants supplied)")

    output_path = (args.output or work_dir / "score.json").resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(asdict(score), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n score saved to: {output_path}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score a generated model.spthy against a dataset entry"
    )
    parser.add_argument(
        "--model",
        type=Path,
        required=True,
        help="Path to the generated model.spthy",
    )
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        required=True,
        help="Dataset root containing metadata.yaml and per-protocol entries",
    )
    parser.add_argument(
        "--protocol-id",
        required=True,
        help="Protocol id under metadata.yaml to score against",
    )
    parser.add_argument(
        "--tamarin-timeout",
        type=int,
        default=300,
        help="Per-Tamarin-invocation timeout in seconds",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Where to write score.json (default: <model_dir>/score.json)",
    )
    return parser


if __name__ == "__main__":
    sys.exit(main())
