"""Score all protocol runs produced by ablation experiments.

Scans ``<runs_dir>/<ablation_system>/<label>/<protocol>/run_<i>/``
for every ablation system listed in ``--systems``, invokes
``scripts.score_protocol`` per run (skipping runs already scored),
and records outcomes in ``run_<i>/score.status``.

Usage
-----
    python -m scripts.score_ablation --systems masta_no_arbiter,masta_merged
    python -m scripts.score_ablation --systems masta_no_arbiter --parallel 4
    python -m scripts.score_ablation --systems masta_merged --retry-failed
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from experiments._common import (
    DEFAULT_CONFIG_PATH,
    ExperimentConfigError,
    load_experiment_config,
    write_status,
)


@dataclass(frozen=True)
class RunEntry:
    system: str
    label: str
    protocol_id: str
    run_dir: Path


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        cfg = load_experiment_config(args.config)
    except ExperimentConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    systems = [s.strip() for s in args.systems.split(",") if s.strip()]
    parallel = args.parallel if args.parallel is not None else cfg.parallel

    print("==== MASTA score_ablation ====")
    print(f" systems:      {', '.join(systems)}")
    print(f" runs_dir:     {cfg.runs_dir}")
    print(f" parallel:     {parallel}")
    print(f" retry-failed: {args.retry_failed}")
    print()

    all_runs = _collect_runs(cfg.runs_dir, systems)
    print(f" total runs found: {len(all_runs)}")

    pending: list[RunEntry] = []
    done = 0
    for run in all_runs:
        if (run.run_dir / "score.json").is_file():
            done += 1
            continue
        if not args.retry_failed:
            status_path = run.run_dir / "score.status"
            if status_path.is_file():
                try:
                    outcome = json.loads(status_path.read_text())["outcome"]
                    if outcome != "ok":
                        continue
                except Exception:
                    pass
        pending.append(run)

    print(f" already scored: {done}")
    print(f" to score:       {len(pending)}")
    print()

    if not pending:
        return 0

    succeeded = 0
    failed: list[tuple[str, str]] = []
    with ProcessPoolExecutor(max_workers=max(1, parallel)) as pool:
        futures = {pool.submit(_score_one, run, cfg): run for run in pending}
        for fut in as_completed(futures):
            run = futures[fut]
            tag = f"  [{succeeded + len(failed) + 1}/{len(pending)}]"
            label = f"{run.system}/{run.label}/{run.protocol_id}/{run.run_dir.name}"
            try:
                outcome = fut.result()
            except Exception as exc:
                failed.append((label, f"crash: {exc}"))
                print(f"{tag} FAIL {label} (crash {exc!r})")
                continue
            if outcome == "ok":
                succeeded += 1
                print(f"{tag} OK   {label}")
            else:
                failed.append((label, outcome))
                print(f"{tag} FAIL {label} ({outcome})")

    print()
    print(f"  succeeded={succeeded}  failed={len(failed)}  already_done={done}")
    if failed:
        for label, reason in failed:
            print(f"    - {label} ({reason})")
        return 1
    return 0


def _collect_runs(runs_dir: Path, systems: list[str]) -> list[RunEntry]:
    entries: list[RunEntry] = []
    for sys_name in systems:
        sys_dir = runs_dir / sys_name
        if not sys_dir.is_dir():
            continue
        for label_dir in sorted(sys_dir.iterdir()):
            if not label_dir.is_dir():
                continue
            for proto_dir in sorted(label_dir.iterdir()):
                if not proto_dir.is_dir():
                    continue
                for run_dir in sorted(proto_dir.glob("run_*")):
                    if not run_dir.is_dir():
                        continue
                    entries.append(
                        RunEntry(
                            system=sys_name,
                            label=label_dir.name,
                            protocol_id=proto_dir.name,
                            run_dir=run_dir,
                        )
                    )
    return entries


def _score_one(run: RunEntry, cfg) -> str:
    cmd = [
        sys.executable, "-m", "scripts.score_protocol",
        "--run-dir", str(run.run_dir),
        "--dataset-dir", str(cfg.dataset_dir),
        "--protocol-id", run.protocol_id,
        "--tamarin-timeout", str(cfg.tamarin_timeout_seconds),
    ]
    log_path = run.run_dir / "score.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"$ {' '.join(cmd)}\n\n")
        log.flush()
        try:
            proc = subprocess.run(
                cmd, stdout=log, stderr=subprocess.STDOUT,
                timeout=cfg.task_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            log.write(f"\n[score timeout after {cfg.task_timeout_seconds}s]\n")
            outcome = "score timeout"
            write_status(run.run_dir, "score", outcome)
            return outcome
    if proc.returncode != 0:
        outcome = f"score exit={proc.returncode}"
        write_status(run.run_dir, "score", outcome)
        return outcome
    return "ok"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Score all protocol runs under ablation system directories "
            "by invoking scripts.score_protocol in a worker pool."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument(
        "--systems", required=True,
        help="Comma-separated ablation system names, e.g. masta_no_arbiter,masta_merged",
    )
    parser.add_argument("--parallel", type=int, default=None)
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="Re-attempt runs whose score.status recorded a failure.",
    )
    return parser


if __name__ == "__main__":
    sys.exit(main())
