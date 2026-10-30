"""Score the runs tree at one or both levels.

Two scoring levels:

* ``base`` — for every base ``model.spthy`` under
  ``runs/<system>/<label>/<protocol>/run_<i>/base/``, runs
  ``scripts.score_model`` and writes ``base/score.json``. Captures
  M1 / M2 / M3 only; M4 / M5 are placeholders (no variant inputs).

* ``protocol`` — for every ``run_<i>/`` whose base + every expected
  variant ``model.spthy`` are present, runs ``scripts.score_protocol``
  and writes ``run_<i>/score.json`` with M1-M5 fully populated.

``--level base|protocol|all`` picks the level(s); default ``all``.
``--system`` limits the systems considered; ``--retry-failed`` re-runs
attempts whose ``*.status`` recorded a failure.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

from experiments._common import (
    DEFAULT_CONFIG_PATH,
    KNOWN_EXPERIMENTS,
    ExperimentConfig,
    ExperimentConfigError,
    ProtocolJob,
    ProtocolRunJob,
    enumerate_jobs,
    enumerate_protocol_runs,
    load_experiment_config,
    output_root_for,
    run_base_score,
    run_protocol_score,
    select_protocol_score_jobs,
    select_score_jobs,
    write_status,
)


load_dotenv()


SCORE_LEVELS = ("base", "protocol", "all")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        cfg = load_experiment_config(args.config)
    except ExperimentConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    selected_systems = _resolve_systems(args.system, cfg)
    selected_labels: set[str] | None = (
        {s.strip() for s in args.label.split(",") if s.strip()}
        if args.label else None
    )
    parallel = args.parallel if args.parallel is not None else cfg.parallel
    levels = ("base", "protocol") if args.level == "all" else (args.level,)

    print("==== MASTA run_score ====")
    print(f" config:       {args.config}")
    print(f" dataset_dir:  {cfg.dataset_dir}")
    print(f" systems:      {', '.join(selected_systems)}")
    if selected_labels is not None:
        print(f" labels:       {', '.join(sorted(selected_labels))}")
    print(f" levels:       {', '.join(levels)}")
    print(f" parallel:     {parallel}")
    print(f" task timeout: {cfg.task_timeout_seconds}s")
    print(f" retry-failed: {args.retry_failed}")
    print(f" force:        {args.force}")

    overall_failed = 0
    for level in levels:
        for system_name in selected_systems:
            system = cfg.system_for(system_name)
            for entry in system.models:
                if selected_labels is not None and entry.label not in selected_labels:
                    continue
                output_root = output_root_for(
                    cfg, system=system_name, label=entry.label
                )
                if level == "base":
                    rc = _score_base_subtree(
                        cfg=cfg,
                        system_name=system_name,
                        label=entry.label,
                        output_root=output_root,
                        parallel=parallel,
                        retry_failed=args.retry_failed,
                        force=args.force,
                    )
                else:
                    rc = _score_protocol_subtree(
                        cfg=cfg,
                        system_name=system_name,
                        label=entry.label,
                        output_root=output_root,
                        parallel=parallel,
                        retry_failed=args.retry_failed,
                        force=args.force,
                    )
                if rc != 0:
                    overall_failed += 1
    return 0 if overall_failed == 0 else 1


def _resolve_systems(
    requested: str | None, cfg: ExperimentConfig
) -> tuple[str, ...]:
    if requested:
        names = [s.strip() for s in requested.split(",") if s.strip()]
        unknown = [n for n in names if n not in KNOWN_EXPERIMENTS]
        if unknown:
            raise SystemExit(
                f"unknown systems in --system: {unknown}; allowed: "
                f"{list(KNOWN_EXPERIMENTS)}"
            )
        seen: list[str] = []
        for n in names:
            if n in cfg.systems and n not in seen:
                seen.append(n)
        return tuple(seen)
    return tuple(name for name in KNOWN_EXPERIMENTS if name in cfg.systems)


def _score_base_subtree(
    *,
    cfg: ExperimentConfig,
    system_name: str,
    label: str,
    output_root: Path,
    parallel: int,
    retry_failed: bool,
    force: bool = False,
) -> int:
    if not output_root.is_dir():
        print(f"\n[base] --- {system_name}/{label} (skip: no runs directory) ---")
        return 0

    base_jobs = enumerate_jobs(
        cfg, output_root=output_root, include_variants=False,
    )
    if force:
        pending = [j for j in base_jobs if (j.output_dir / "model.spthy").is_file()]
        missing_model = len(base_jobs) - len(pending)
        selection = type("S", (), {
            "done": 0, "prior_failures": 0,
            "prior_failure_outcomes": {},
        })()
    else:
        selection, missing_model = select_score_jobs(
            base_jobs, retry_failed=retry_failed
        )
        pending = selection.pending

    print(f"\n[base] --- {system_name}/{label} ---")
    print(f"  output_root:    {output_root}")
    print(f"  base jobs:      {len(base_jobs)}")
    print(f"  done:           {selection.done} (score.json present)")
    print(f"  no model:       {missing_model}")
    if selection.prior_failures:
        breakdown = ", ".join(
            f"{outcome}={count}"
            for outcome, count in sorted(selection.prior_failure_outcomes.items())
        )
        print(f"  prior failures: {selection.prior_failures} ({breakdown})")
    print(f"  to_score:       {len(pending)}")

    if not pending:
        return 0

    succeeded = 0
    failed: list[tuple[str, str]] = []
    with ProcessPoolExecutor(max_workers=max(1, parallel)) as pool:
        futures = {pool.submit(_score_base_one, job, cfg): job for job in pending}
        for fut in as_completed(futures):
            job = futures[fut]
            tag = f"  [{succeeded + len(failed) + 1}/{len(pending)}]"
            try:
                outcome = fut.result()
            except Exception as exc:
                failed.append((job.label, f"crash: {exc}"))
                print(f"{tag} FAIL {job.label} (crash {exc!r})")
                continue
            if outcome == "ok":
                succeeded += 1
                print(f"{tag} OK   {job.label}")
            else:
                failed.append((job.label, outcome))
                print(f"{tag} FAIL {job.label} ({outcome})")

    print(
        f"  --- [base] {system_name}/{label}: succeeded={succeeded} "
        f"failed={len(failed)} done={selection.done} "
        f"prior_failures={selection.prior_failures} ---"
    )
    if failed:
        for tag, reason in failed:
            print(f"    - {tag} ({reason})")
        return 1
    return 0


def _score_base_one(job: ProtocolJob, cfg: ExperimentConfig) -> str:
    rc = run_base_score(
        job=job,
        dataset_dir=cfg.dataset_dir,
        tamarin_timeout=cfg.tamarin_timeout_seconds,
        task_timeout=cfg.task_timeout_seconds,
    )
    if rc is None:
        outcome = "no model.spthy"
        write_status(job.output_dir, "score", outcome)
        return outcome
    if rc == "timeout":
        outcome = "score timeout"
        write_status(job.output_dir, "score", outcome)
        return outcome
    if rc != 0:
        outcome = f"score exit={rc}"
        write_status(job.output_dir, "score", outcome)
        return outcome
    return "ok"


def _score_protocol_subtree(
    *,
    cfg: ExperimentConfig,
    system_name: str,
    label: str,
    output_root: Path,
    parallel: int,
    retry_failed: bool,
    force: bool = False,
) -> int:
    if not output_root.is_dir():
        print(
            f"\n[protocol] --- {system_name}/{label} "
            f"(skip: no runs directory) ---"
        )
        return 0

    run_jobs = enumerate_protocol_runs(cfg, output_root=output_root)
    if force:
        pending = [rj for rj in run_jobs if rj.run_dir.is_dir()]
        selection = type("S", (), {
            "done": 0, "incomplete": len(run_jobs) - len(pending),
            "prior_failures": 0, "prior_failure_outcomes": {},
        })()
    else:
        selection = select_protocol_score_jobs(
            run_jobs,
            retry_failed=retry_failed,
        )
        pending = selection.pending

    print(f"\n[protocol] --- {system_name}/{label} ---")
    print(f"  output_root:    {output_root}")
    print(f"  protocol runs:  {len(run_jobs)}")
    print(f"  done:           {selection.done} (score.json present)")
    print(f"  incomplete:     {selection.incomplete} (missing base/variant model)")
    if selection.prior_failures:
        breakdown = ", ".join(
            f"{outcome}={count}"
            for outcome, count in sorted(selection.prior_failure_outcomes.items())
        )
        print(f"  prior failures: {selection.prior_failures} ({breakdown})")
    print(f"  to_score:       {len(pending)}")

    if not pending:
        return 0

    succeeded = 0
    failed: list[tuple[str, str]] = []
    with ProcessPoolExecutor(max_workers=max(1, parallel)) as pool:
        futures = {
            pool.submit(_score_protocol_one, rj, cfg, force): rj for rj in pending
        }
        for fut in as_completed(futures):
            rj = futures[fut]
            tag = f"  [{succeeded + len(failed) + 1}/{len(pending)}]"
            try:
                outcome = fut.result()
            except Exception as exc:
                failed.append((rj.label, f"crash: {exc}"))
                print(f"{tag} FAIL {rj.label} (crash {exc!r})")
                continue
            if outcome == "ok":
                succeeded += 1
                print(f"{tag} OK   {rj.label}")
            else:
                failed.append((rj.label, outcome))
                print(f"{tag} FAIL {rj.label} ({outcome})")

    print(
        f"  --- [protocol] {system_name}/{label}: succeeded={succeeded} "
        f"failed={len(failed)} done={selection.done} "
        f"prior_failures={selection.prior_failures} ---"
    )
    if failed:
        for tag, reason in failed:
            print(f"    - {tag} ({reason})")
        return 1
    return 0


def _score_protocol_one(
    rj: ProtocolRunJob, cfg: ExperimentConfig, force: bool = False
) -> str:
    rc = run_protocol_score(
        run_job=rj,
        dataset_dir=cfg.dataset_dir,
        tamarin_timeout=cfg.tamarin_timeout_seconds,
        task_timeout=cfg.task_timeout_seconds,
        force=force,
    )
    if rc is None:
        outcome = "no base model.spthy"
        write_status(rj.run_dir, "score", outcome)
        return outcome
    if rc == "timeout":
        outcome = "score timeout"
        write_status(rj.run_dir, "score", outcome)
        return outcome
    if rc != 0:
        outcome = f"score exit={rc}"
        write_status(rj.run_dir, "score", outcome)
        return outcome
    return "ok"



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Score the runs tree at base level (M1/M2/M3) and/or protocol "
            "level (M1-M5) by invoking scripts.score_model and "
            "scripts.score_protocol in worker pools."
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
            f"{','.join(KNOWN_EXPERIMENTS)}; defaults to every system "
            "declared in the config."
        ),
    )
    parser.add_argument(
        "--label",
        default=None,
        help=(
            "Comma-separated subset of model labels (the YAML key under "
            "providers, or 'default_<tag>') to score; defaults to every "
            "label declared under each selected system."
        ),
    )
    parser.add_argument(
        "--level",
        choices=list(SCORE_LEVELS),
        default="all",
        help=(
            "Which scoring level to run: 'base' (per-base M1/M2/M3), "
            "'protocol' (per-run_<i> M1-M5), 'all' (both, base first). "
            "Default: all."
        ),
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=None,
        help="Override config.parallel for this run",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help=(
            "Re-attempt scoring jobs whose previous attempt timed out or "
            "exited non-zero (default: skip them based on score.status)."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-score every run that has a model.spthy, overwriting existing score.json.",
    )
    return parser


if __name__ == "__main__":
    sys.exit(main())
