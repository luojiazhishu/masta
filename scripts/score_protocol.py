"""Score a full protocol run (base + variants) for one ``run_<i>/`` tree.

Aggregates the products of one complete protocol run into a single
``ProtocolPrediction`` and runs ``score_protocol`` against the dataset
spec, writing M1-M5 to ``<run_dir>/score.json``.

Scoring policy:
- If ``<run_dir>/score.json`` already exists the script exits immediately
  and reuses the previous result.
- If ``base/model.spthy`` is missing or empty all five metrics are 0.0.
- For each action / weakness variant whose ``model.spthy`` is absent,
  that variant contributes 0 to M4 / M5 rather than being excluded from
  the denominator.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from src.evaluation.dataset_loader import load_dataset
from src.evaluation.metrics import align_by_similarity, compute_m1
from src.evaluation.protocol_score import score_protocol
from src.model.protocol_prediction import ProtocolPrediction
from src.model.score import ProtocolScore
from src.model.verdict import TamarinVerdict
from src.tamarin.runner import (
    TamarinRunner,
    extract_lemma_definition,
    extract_m1_action_blocks,
    extract_safety_lemma_names,
)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    run_dir = args.run_dir.resolve()
    if not run_dir.is_dir():
        print(f"ERROR: run dir not found: {run_dir}", file=sys.stderr)
        return 2

    output_path = (args.output or run_dir / "score.json").resolve()
    if output_path.is_file() and not args.force:
        print(f" already scored — reusing {output_path}")
        return 0

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

    print("==== MASTA score_protocol ====")
    print(f" run_dir:     {run_dir}")
    print(f" dataset_dir: {dataset_dir}")
    print(f" protocol:    {spec.id}")

    base_model = run_dir / "base" / "model.spthy"
    base_missing = (
        not base_model.is_file()
        or not base_model.read_text(encoding="utf-8").strip()
    )
    if base_missing:
        print(" base/model.spthy missing or empty — writing zero score")
        score = ProtocolScore(
            protocol_id=spec.id,
            m1=0.0, m2=0.0, m3=0.0, m4=0.0, m5=0.0,
        )
        _write_score(score, output_path)
        print(f"\n score saved to: {output_path}")
        return 0

    runner = TamarinRunner(timeout_seconds=args.tamarin_timeout)

    # ---------- base side ----------
    base_dir = run_dir / "base"
    yaml_path = extract_m1_action_blocks(
        runner=runner, work_dir=base_dir, output_dir=base_dir
    )
    base_action_blocks_text = (
        yaml_path.read_text(encoding="utf-8")
        if yaml_path is not None and yaml_path.exists()
        else ""
    )

    base_spec_text = base_model.read_text(encoding="utf-8")
    safety_names = list(extract_safety_lemma_names(base_spec_text))
    base_lemma_definitions: dict[str, str] = {
        name: (extract_lemma_definition(base_spec_text, name) or "")
        for name in safety_names
    }
    base_lemma_verdicts: dict[str, TamarinVerdict | None] = {}
    base_lemma_steps: dict[str, int | None] = {}
    for name in safety_names:
        result = runner.verify_lemma(base_spec_text, name)
        verdict = getattr(result, "verdict", None)
        if not isinstance(verdict, TamarinVerdict):
            verdict = TamarinVerdict.FALSIFIED
        base_lemma_verdicts[name] = verdict
        base_lemma_steps[name] = getattr(result, "steps", None)

    # name_map needed for per-variant M5 scoring
    name_map = align_by_similarity(spec.baseline_lemmas, base_lemma_definitions)

    # ---------- action variants: M4 ----------
    action_variant_blocks_texts: dict[str, str] = {}
    av_missing = 0
    for av in spec.action_variants:
        av_dir = run_dir / "action_variants" / av.id
        av_score_path = av_dir / "score.json"
        av_model = av_dir / "model.spthy"
        if not av_model.is_file() or not av_model.read_text(encoding="utf-8").strip():
            print(f" [av {av.id}] model.spthy missing — counted as 0")
            action_variant_blocks_texts[av.id] = ""
            av_missing += 1
            if not av_score_path.is_file() or args.force:
                _write_json(av_score_path, {"variant_id": av.id, "type": "action_variant", "score": 0.0})
            continue
        yaml_path = extract_m1_action_blocks(
            runner=runner, work_dir=av_dir, output_dir=av_dir
        )
        text = (
            yaml_path.read_text(encoding="utf-8")
            if yaml_path is not None and yaml_path.exists()
            else ""
        )
        action_variant_blocks_texts[av.id] = text
        if not av_score_path.is_file() or args.force:
            from src.evaluation.action_block_parser import parse_action_blocks
            from src.evaluation.action_block_parser import ActionBlockParseError
            baseline_blocks = parse_action_blocks(av.baseline_blocks_text)
            try:
                predicted_blocks = parse_action_blocks(text) if text.strip() else ()
            except ActionBlockParseError:
                predicted_blocks = ()
            av_score = compute_m1(predicted_blocks, baseline_blocks)
            _write_json(av_score_path, {"variant_id": av.id, "type": "action_variant", "score": round(av_score, 6)})

    # ---------- weakness variants: M5 ----------
    weakness_variant_verdicts: dict[str, dict[str, TamarinVerdict | None]] = {}
    wv_missing = 0
    for wv in spec.weakness_variants:
        wv_dir = run_dir / "weakness_variants" / wv.id
        wv_score_path = wv_dir / "score.json"
        wv_model = wv_dir / "model.spthy"
        target_lemmas = list(wv.falsified_lemmas)
        if not wv_model.is_file() or not wv_model.read_text(encoding="utf-8").strip():
            print(f" [wv {wv.id}] model.spthy missing — counted as 0")
            weakness_variant_verdicts[wv.id] = {}
            wv_missing += 1
            if not wv_score_path.is_file() or args.force:
                total = len(target_lemmas)
                _write_json(wv_score_path, {
                    "variant_id": wv.id, "type": "weakness_variant",
                    "score": 0.0, "hits": 0, "total": total,
                })
            continue
        wv_spec_text = wv_model.read_text(encoding="utf-8")
        per_variant: dict[str, TamarinVerdict | None] = {}
        for name in safety_names:
            result = runner.verify_lemma(wv_spec_text, name)
            verdict = getattr(result, "verdict", None)
            if not isinstance(verdict, TamarinVerdict):
                verdict = TamarinVerdict.FALSIFIED
            per_variant[name] = verdict
        weakness_variant_verdicts[wv.id] = per_variant
        if not wv_score_path.is_file() or args.force:
            total = len(target_lemmas)
            hits = sum(
                1 for lem in target_lemmas
                if (agent_name := name_map.get(lem)) is not None
                and per_variant.get(agent_name) is TamarinVerdict.FALSIFIED
            )
            wv_score = hits / total if total > 0 else 1.0
            _write_json(wv_score_path, {
                "variant_id": wv.id, "type": "weakness_variant",
                "score": round(wv_score, 6), "hits": hits, "total": total,
            })

    prediction = ProtocolPrediction(
        protocol_id=spec.id,
        base_action_blocks_text=base_action_blocks_text,
        base_lemma_definitions=base_lemma_definitions,
        base_lemma_verdicts=base_lemma_verdicts,
        base_lemma_steps=base_lemma_steps,
        action_variant_blocks_texts=action_variant_blocks_texts,
        weakness_variant_verdicts=weakness_variant_verdicts,
    )
    score = score_protocol(spec, prediction)

    print()
    print("==== Score ====")
    print(f" protocol: {spec.id}")
    print(f" M1: {score.m1:.3f}")
    print(f" M2: {score.m2:.3f}")
    print(f" M3: {score.m3:.3f}")
    n_av = len(spec.action_variants)
    n_wv = len(spec.weakness_variants)
    print(
        f" M4: {score.m4:.3f}  "
        f"({n_av - av_missing}/{n_av} action variants present)"
    )
    print(
        f" M5: {score.m5:.3f}  "
        f"({n_wv - wv_missing}/{n_wv} weakness variants present)"
    )

    _write_score(score, output_path)
    print(f"\n score saved to: {output_path}")
    return 0


def _write_score(score: ProtocolScore, output_path: Path) -> None:
    _write_json(output_path, asdict(score))


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Score a complete protocol run (base + variants) under one "
            "run_<i>/ tree against a dataset entry, producing M1-M5."
        )
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        required=True,
        help=(
            "Path to the run_<i>/ root containing base/, action_variants/, "
            "weakness_variants/ subtrees"
        ),
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
        help="Where to write score.json (default: <run_dir>/score.json)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run scoring even if score.json already exists.",
    )
    return parser


if __name__ == "__main__":
    sys.exit(main())
