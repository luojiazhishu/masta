"""Summarize ablation experiment results into a single JSON file.

Scans ``<runs_dir>/<ablation_system>/<label>/<protocol>/run_<i>/score.json``
for every ablation system listed in ``--systems`` and writes a summary
JSON. Runs whose score.json is absent are recorded as ``not_scored``
with null metric values. Optional protocol filtering applies to every system.
Per-system/model metric and composite means are included in the output.

Usage
-----
    python -m scripts.summarize_ablation --systems masta_no_arbiter,masta_merged
    python -m scripts.summarize_ablation --systems masta_merged --output ablation.json
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path
from statistics import fmean

from experiments._common import (
    DEFAULT_CONFIG_PATH,
    ExperimentConfigError,
    load_experiment_config,
)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        cfg = load_experiment_config(args.config)
    except ExperimentConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    systems = [s.strip() for s in args.systems.split(",") if s.strip()]
    protocols = (
        {p.strip() for p in args.protocols.split(",") if p.strip()}
        if args.protocols else None
    )
    output_path = args.output.resolve()

    print("==== MASTA summarize_ablation ====")
    print(f" systems:  {', '.join(systems)}")
    print(f" runs_dir: {cfg.runs_dir}")
    print(f" output:   {output_path}")
    print()

    runs: list[dict] = []
    counts: dict[str, int] = dict(total=0, scored=0, not_scored=0)

    for sys_name in systems:
        sys_dir = cfg.runs_dir / sys_name
        if not sys_dir.is_dir():
            print(f"  [skip] {sys_name}: directory not found")
            continue
        for label_dir in sorted(sys_dir.iterdir()):
            if not label_dir.is_dir():
                continue
            for proto_dir in sorted(label_dir.iterdir()):
                if not proto_dir.is_dir():
                    continue
                if protocols is not None and proto_dir.name not in protocols:
                    continue
                for run_dir in sorted(proto_dir.glob("run_*")):
                    if not run_dir.is_dir():
                        continue
                    counts["total"] += 1
                    row = _make_row(
                        sys_name, label_dir.name,
                        proto_dir.name, run_dir,
                    )
                    runs.append(row)
                    if row["status"] == "scored":
                        counts["scored"] += 1
                        scores = "  ".join(
                            f"M{i+1}={row[f'm{i+1}']:.3f}"
                            for i in range(5)
                            if row[f"m{i+1}"] is not None
                        )
                        print(
                            f"  [scored]     "
                            f"{sys_name}/{label_dir.name}/{proto_dir.name}"
                            f"/{run_dir.name}  {scores}"
                        )
                    else:
                        counts["not_scored"] += 1
                        print(
                            f"  [not_scored] "
                            f"{sys_name}/{label_dir.name}/{proto_dir.name}"
                            f"/{run_dir.name}"
                        )

    print()
    print("==== Summary ====")
    print(f"  total:      {counts['total']}")
    print(f"  scored:     {counts['scored']}")
    print(f"  not_scored: {counts['not_scored']}")

    # Per-system per-label means
    from collections import defaultdict
    means: dict[tuple[str, str], dict] = defaultdict(lambda: defaultdict(list))
    for r in runs:
        if r["status"] != "scored":
            continue
        key = (r["system"], r["label"])
        for m in ("m1", "m2", "m3", "m4", "m5"):
            if r[m] is not None:
                means[key][m].append(r[m])

    mean_rows = []
    if means:
        print()
        print("==== Per-system/label means ====")
        for key in sorted(means):
            vals = means[key]
            metric_means = {
                m: fmean(vals[m]) if vals.get(m) else None
                for m in ("m1", "m2", "m3", "m4", "m5")
            }
            for name, metrics in (
                ("correctness", ("m1", "m2", "m3")),
                ("completeness", ("m4", "m5")),
            ):
                values = [metric_means[m] for m in metrics]
                metric_means[name] = (
                    fmean(values) if all(v is not None for v in values) else None
                )
            mean_rows.append({
                "system": key[0],
                "model": key[1],
                "protocol_count": len({
                    r["protocol"] for r in runs
                    if r["status"] == "scored"
                    and (r["system"], r["label"]) == key
                }),
                **metric_means,
            })
            parts = "  ".join(
                f"M{i+1}={metric_means[f'm{i+1}']:.3f}"
                if vals.get(f"m{i+1}") else f"M{i+1}=n/a"
                for i in range(5)
            )
            print(f"  {'/'.join(key)}: {parts}")

    payload = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "systems": systems,
        "summary": counts,
        "runs": runs,
        "means": mean_rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n results written to: {output_path}")
    return 0


def _make_row(system: str, label: str, protocol_id: str, run_dir: Path) -> dict:
    base = {
        "system": system,
        "label": label,
        "protocol": protocol_id,
        "run": run_dir.name,
    }
    score_path = run_dir / "score.json"
    if score_path.is_file():
        try:
            data = json.loads(score_path.read_text(encoding="utf-8"))
            return {
                **base,
                "status": "scored",
                "m1": data.get("m1"),
                "m2": data.get("m2"),
                "m3": data.get("m3"),
                "m4": data.get("m4"),
                "m5": data.get("m5"),
            }
        except (OSError, json.JSONDecodeError):
            pass
    return {
        **base,
        "status": "not_scored",
        "m1": None, "m2": None, "m3": None, "m4": None, "m5": None,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Collect ablation experiment scores into a summary JSON file."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument(
        "--systems", required=True,
        help="Comma-separated ablation system names, e.g. masta_no_arbiter,masta_merged",
    )
    parser.add_argument(
        "--protocols",
        help="Comma-separated protocol ids to include for every system; default: all.",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("ablation_results.json"),
        help="Output JSON path (default: ablation_results.json)",
    )
    return parser


if __name__ == "__main__":
    sys.exit(main())
