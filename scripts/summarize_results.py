"""Walk the runs tree, trigger scoring for any un-scored run, and write
a summary JSON file with per-run M1-M5 scores.

For every (system, label, protocol, run_i) tuple defined by the config:

  * base/model.spthy present but score.json absent  →  call score_protocol
    first, then read the result.
  * base/model.spthy absent                         →  record as "not_generated".
  * score.json already present                      →  read the existing result.

Output JSON format::

    {
      "generated_at": "...",
      "summary": {
        "total": N, "scored": N, "not_generated": N, "score_failed": N
      },
      "runs": [
        {
          "system": "masta", "label": "qwen3.5-plus",
          "protocol": "001_cr_xor", "run": 1,
          "status": "scored",     // "scored" | "not_generated" | "score_failed"
          "m1": 0.8, "m2": 0.7, "m3": 0.9, "m4": 0.6, "m5": 0.5
        },
        ...
      ]
    }
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

from experiments._common import (
    DEFAULT_CONFIG_PATH,
    KNOWN_EXPERIMENTS,
    ExperimentConfigError,
    ProtocolRunJob,
    enumerate_protocol_runs,
    load_experiment_config,
    output_root_for,
)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        cfg = load_experiment_config(args.config)
    except ExperimentConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    systems = _resolve_filter(args.system, tuple(KNOWN_EXPERIMENTS))
    labels = _resolve_filter(args.label, None)
    output_path = args.output.resolve() if args.output else Path("results.json")

    print("==== MASTA summarize_results ====")
    print(f" config:   {args.config}")
    print(f" runs_dir: {cfg.runs_dir}")
    print(f" output:   {output_path}")
    print()

    all_runs: list[dict] = []
    counts = dict(total=0, scored=0, timeout=0, not_generated=0, score_failed=0)

    for system_name in KNOWN_EXPERIMENTS:
        if system_name not in cfg.systems:
            continue
        if systems and system_name not in systems:
            continue
        sys_cfg = cfg.system_for(system_name)
        for entry in sys_cfg.models:
            if labels and entry.label not in labels:
                continue
            output_root = output_root_for(cfg, system=system_name, label=entry.label)
            run_jobs = enumerate_protocol_runs(cfg, output_root=output_root)

            for rj in run_jobs:
                counts["total"] += 1
                row = _process_run(rj, cfg, system_name, entry.label, args)
                all_runs.append(row)
                _print_row(row)
                counts[row["status"]] += 1 if row["status"] != "scored" else 0
                if row["status"] == "scored":
                    counts["scored"] += 1

    print()
    print("==== Summary ====")
    print(f"  total:          {counts['total']}")
    print(f"  scored:         {counts['scored']}")
    print(f"  timeout:        {counts['timeout']}")
    print(f"  not_generated:  {counts['not_generated']}")
    print(f"  score_failed:   {counts['score_failed']}")

    payload = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "summary": counts,
        "runs": all_runs,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n results written to: {output_path}")
    return 0


def _process_run(
    rj: ProtocolRunJob,
    cfg,
    system_name: str,
    label: str,
    args: argparse.Namespace,
) -> dict:
    base = {
        "system": system_name,
        "label": label,
        "protocol": rj.protocol_id,
        "run": int(rj.run_dir.name.removeprefix("run_")),
    }
    score_path = rj.run_dir / "score.json"
    base_model = rj.run_dir / "base" / "model.spthy"
    timed_out = _gen_timed_out(rj.run_dir)

    # Already scored — promote to "timeout" when the upstream generation
    # phase (recorded in ``base/gen.status``) hit its wall-clock limit so
    # the figures still get an all-zero data point but the summary makes
    # the failure mode explicit.
    if score_path.is_file():
        status = "timeout" if timed_out else "scored"
        return {**base, "status": status, **_read_scores(score_path)}

    # No base model
    if not _nonempty_file(base_model):
        status = "timeout" if timed_out else "not_generated"
        return {
            **base, "status": status,
            "m1": None, "m2": None, "m3": None, "m4": None, "m5": None,
        }

    # Has model but no score → trigger scoring
    rc = _score_run(rj, cfg, args)
    if rc == 0 and score_path.is_file():
        return {**base, "status": "scored", **_read_scores(score_path)}
    return {
        **base, "status": "score_failed",
        "m1": None, "m2": None, "m3": None, "m4": None, "m5": None,
    }


def _score_run(rj: ProtocolRunJob, cfg, args: argparse.Namespace) -> int:
    cmd = [
        sys.executable, "-m", "scripts.score_protocol",
        "--run-dir", str(rj.run_dir),
        "--dataset-dir", str(cfg.dataset_dir),
        "--protocol-id", rj.protocol_id,
        "--tamarin-timeout", str(cfg.tamarin_timeout_seconds),
    ]
    log_path = rj.run_dir / "score.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"$ {' '.join(cmd)}\n\n")
        log.flush()
        try:
            proc = subprocess.run(
                cmd, stdout=log, stderr=subprocess.STDOUT,
                timeout=cfg.task_timeout_seconds,
            )
            return proc.returncode
        except subprocess.TimeoutExpired:
            log.write(f"\n[score timeout after {cfg.task_timeout_seconds}s]\n")
            return -1


def _read_scores(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            "m1": data.get("m1"),
            "m2": data.get("m2"),
            "m3": data.get("m3"),
            "m4": data.get("m4"),
            "m5": data.get("m5"),
        }
    except (OSError, json.JSONDecodeError):
        return {"m1": None, "m2": None, "m3": None, "m4": None, "m5": None}


def _gen_timed_out(run_dir: Path) -> bool:
    """Return True when ``base/gen.status`` records a generation timeout.

    Looks for ``"timeout"`` (case-insensitive) inside the JSON ``outcome``
    field; tolerant of malformed status files (treated as "not a timeout").
    """
    status_path = run_dir / "base" / "gen.status"
    if not status_path.is_file():
        return False
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    outcome = data.get("outcome")
    return isinstance(outcome, str) and "timeout" in outcome.lower()


def _nonempty_file(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def _print_row(row: dict) -> None:
    status = row["status"]
    label = f"{row['system']}/{row['label']}/{row['protocol']}/run_{row['run']}"
    if status == "scored":
        scores = " ".join(
            f"M{i+1}={row[f'm{i+1}']:.3f}" for i in range(5)
            if row[f'm{i+1}'] is not None
        )
        print(f"  [scored]         {label:<60s}  {scores}")
    elif status == "timeout":
        scores = " ".join(
            f"M{i+1}={row[f'm{i+1}']:.3f}" for i in range(5)
            if row[f'm{i+1}'] is not None
        )
        print(f"  [timeout]        {label:<60s}  {scores}")
    elif status == "not_generated":
        print(f"  [not_generated]  {label}")
    else:
        print(f"  [score_failed]   {label}")


def _resolve_filter(raw: str | None, allowed: tuple[str, ...] | None) -> set[str]:
    if not raw:
        return set()
    items = {s.strip() for s in raw.split(",") if s.strip()}
    if allowed is not None:
        invalid = [n for n in items if n not in allowed]
        if invalid:
            raise SystemExit(f"unknown values: {invalid}; allowed: {list(allowed)}")
    return items


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Walk the runs tree, auto-score un-scored runs, "
            "and write a summary JSON."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to experiment YAML (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--system",
        default=None,
        help=(
            "Comma-separated subset of "
            f"{','.join(KNOWN_EXPERIMENTS)}; defaults to all systems."
        ),
    )
    parser.add_argument(
        "--label",
        default=None,
        help="Comma-separated subset of model labels; defaults to all.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results.json"),
        help="Where to write the summary JSON (default: results.json).",
    )
    return parser


if __name__ == "__main__":
    sys.exit(main())
