"""Run the No-Arbiter ablation over the dataset.

Uses ``systems.masta.models`` from experiment.yaml for provider/model
config. Artifacts land under ``runs/masta_no_arbiter/<label>/...``.
This runner is standalone — it is NOT wired into run_all.py or
KNOWN_EXPERIMENTS; invoke it directly.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

from experiments._common import (
    DEFAULT_CONFIG_PATH,
    ExperimentConfig,
    ExperimentConfigError,
    ModelEntry,
    ProtocolJob,
    apply_provider_env,
    enumerate_jobs,
    is_generated,
    load_experiment_config,
    output_root_for,
    select_generate_jobs,
    write_status,
)

load_dotenv()

SYSTEM_NAME = "masta_no_arbiter"
SOURCE_SYSTEM = "masta"


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        cfg = load_experiment_config(args.config)
    except ExperimentConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    system = cfg.system_for(SOURCE_SYSTEM)
    parallel = args.parallel if args.parallel is not None else cfg.parallel

    print(f"==== MASTA run_{SYSTEM_NAME} ====")
    print(f" config:       {args.config}")
    print(f" dataset_dir:  {cfg.dataset_dir}")
    print(f" models:       {', '.join(e.label for e in system.models)}")
    print(f" runs/proto:   {cfg.runs_per_protocol}")
    print(f" max-protocols:{args.max_protocols or '(all)'}")
    print(f" parallel:     {parallel}")
    print(f" task timeout: {cfg.task_timeout_seconds}s")
    print(f" retry-failed: {args.retry_failed}")

    overall_failed = 0
    for entry in system.models:
        rc = _run_model(
            cfg, entry=entry, parallel=parallel,
            retry_failed=args.retry_failed,
            max_protocols=args.max_protocols,
        )
        if rc != 0:
            overall_failed += 1
    return 0 if overall_failed == 0 else 1


def _run_model(
    cfg: ExperimentConfig,
    *,
    entry: ModelEntry,
    parallel: int,
    retry_failed: bool,
    max_protocols: int | None,
) -> int:
    assert entry.provider_id is not None
    provider = cfg.provider_for(entry.provider_id)
    apply_provider_env(cfg, provider)
    output_root = output_root_for(cfg, system=SYSTEM_NAME, label=entry.label)
    jobs = enumerate_jobs(cfg, output_root=output_root, include_variants=True)
    jobs = _cap_protocols(jobs, max_protocols)

    selection = select_generate_jobs(jobs, retry_failed=retry_failed)
    pending = selection.pending

    print(f"\n--- {entry.label} ({provider.provider} / {provider.model}) ---")
    print(f"  output_root:    {output_root}")
    print(f"  jobs:           {len(jobs)}")
    print(f"  done:           {selection.done} (model.spthy present)")
    if selection.prior_failures:
        breakdown = ", ".join(
            f"{k}={v}" for k, v in sorted(selection.prior_failure_outcomes.items())
        )
        print(f"  prior failures: {selection.prior_failures} ({breakdown})")
    print(f"  to_run:         {len(pending)}")

    if not pending:
        return 0

    succeeded = 0
    failed: list[tuple[str, str]] = []
    with ProcessPoolExecutor(max_workers=max(1, parallel)) as pool:
        futures = {pool.submit(_run_one, job, cfg, provider): job for job in pending}
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
        f"  --- {entry.label}: succeeded={succeeded} "
        f"failed={len(failed)} done={selection.done} "
        f"prior_failures={selection.prior_failures} ---"
    )
    if failed:
        for label, reason in failed:
            print(f"    - {label} ({reason})")
        return 1
    return 0


def _run_one(job: ProtocolJob, cfg: ExperimentConfig, provider) -> str:
    job.output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "scripts.no_arbiter_model",
        "--document", str(job.document),
        "--output-dir", str(job.output_dir),
        "--provider", provider.provider,
        "--model", provider.model,
        "--temperature", str(cfg.llm_temperature),
        "--seed", str(job.seed),
        "--tamarin-timeout", str(cfg.tamarin_timeout_seconds),
    ]
    if provider.api_key:
        cmd += ["--api-key", provider.api_key]
    if provider.base_url:
        cmd += ["--base-url", provider.base_url]
    if cfg.llm_max_tokens is not None:
        cmd += ["--max-tokens", str(cfg.llm_max_tokens)]
    if cfg.llm_reasoning:
        cmd += ["--reasoning"]
    if cfg.llm_reasoning_budget is not None:
        cmd += ["--reasoning-budget", str(cfg.llm_reasoning_budget)]

    log_path = job.output_dir / "run.log"
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"$ {' '.join(cmd)}\n\n")
        log.flush()
        try:
            proc = subprocess.run(
                cmd, stdout=log, stderr=subprocess.STDOUT,
                timeout=cfg.task_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            log.write(f"\n[gen timeout after {cfg.task_timeout_seconds}s]\n")
            outcome = "gen timeout"
            write_status(job.output_dir, "generate", outcome)
            return outcome
    if proc.returncode != 0:
        outcome = f"gen exit={proc.returncode}"
        write_status(job.output_dir, "generate", outcome)
        return outcome
    if not is_generated(job):
        outcome = "no model.spthy"
        write_status(job.output_dir, "generate", outcome)
        return outcome
    return "ok"


def _cap_protocols(
    jobs: list[ProtocolJob], max_protocols: int | None
) -> list[ProtocolJob]:
    if not max_protocols:
        return jobs
    allowed: list[str] = []
    for j in jobs:
        if j.protocol_id not in allowed:
            allowed.append(j.protocol_id)
            if len(allowed) >= max_protocols:
                break
    allowed_set = set(allowed)
    return [j for j in jobs if j.protocol_id in allowed_set]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the No-Arbiter ablation over the dataset. "
            "Uses systems.masta.models for provider config; "
            "artifacts land under runs/masta_no_arbiter/<label>/..."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--parallel", type=int, default=None)
    parser.add_argument(
        "--max-protocols", type=int, default=None,
        help="Only run the first N protocols (alphabetical order). Default: all.",
    )
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="Re-attempt jobs whose previous attempt timed out or exited non-zero.",
    )
    return parser


if __name__ == "__main__":
    sys.exit(main())
