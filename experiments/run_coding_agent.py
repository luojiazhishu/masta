"""Run a coding-agent CLI (codex / claude / opencode) over the dataset.

Drives ``scripts.coding_agent_model`` once per (protocol|variant, run_idx)
under the chosen system. Each ``systems.<name>.models`` entry resolves
to one of two modes:

* ``default(<tag>)`` — the host CLI uses its globally-configured account
  and default model. ``<tag>`` is only used as the artifact subdir name
  (``runs/<system>/default_<tag>/...``).

* a provider id from ``providers`` — credentials and the real model name
  are pushed into the CLI per-run; artifacts land under
  ``runs/<system>/<provider_id>/...``.

This runner only generates ``model.spthy`` per job — scoring is handled
by ``experiments.run_score``.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

from experiments._common import (
    CODING_AGENT_SYSTEMS,
    CODING_AGENT_TOOL,
    DEFAULT_CONFIG_PATH,
    PROJECT_ROOT,
    ExperimentConfig,
    ExperimentConfigError,
    ModelEntry,
    ProtocolJob,
    ProviderEntry,
    enumerate_jobs,
    is_generated,
    load_experiment_config,
    output_root_for,
    select_generate_jobs,
    write_status,
)


load_dotenv()


SKILLS_SOURCE = PROJECT_ROOT / "experiments" / "coding_agent" / "skills"


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        cfg = load_experiment_config(args.config)
    except ExperimentConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.system not in CODING_AGENT_SYSTEMS:
        print(
            f"ERROR: --system must be one of {list(CODING_AGENT_SYSTEMS)}, "
            f"got {args.system!r}",
            file=sys.stderr,
        )
        return 2

    try:
        system = cfg.system_for(args.system)
    except ExperimentConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    parallel = args.parallel if args.parallel is not None else cfg.parallel
    tool = CODING_AGENT_TOOL[args.system]

    print(f"==== MASTA run_coding_agent ({args.system}) ====")
    print(f" config:       {args.config}")
    print(f" dataset_dir:  {cfg.dataset_dir}")
    print(f" tool:         {tool}")
    print(f" entries:      {', '.join(_entry_summary(e, cfg) for e in system.models)}")
    print(f" runs/proto:   {cfg.runs_per_protocol}")
    print(f" parallel:     {parallel}")
    print(f" task timeout: {cfg.task_timeout_seconds}s")
    print(f" retry-failed: {args.retry_failed}")

    overall_failed = 0
    for entry in system.models:
        rc = _run_entry(
            cfg,
            system_name=args.system,
            tool=tool,
            entry=entry,
            parallel=parallel,
            retry_failed=args.retry_failed,
        )
        if rc != 0:
            overall_failed += 1
    return 0 if overall_failed == 0 else 1


def _entry_summary(entry: ModelEntry, cfg: ExperimentConfig) -> str:
    if entry.provider_id is None:
        return f"{entry.label} [default]"
    p = cfg.provider_for(entry.provider_id)
    return f"{entry.label} [{p.provider}/{p.model}]"


def _run_entry(
    cfg: ExperimentConfig,
    *,
    system_name: str,
    tool: str,
    entry: ModelEntry,
    parallel: int,
    retry_failed: bool,
) -> int:
    output_root = output_root_for(cfg, system=system_name, label=entry.label)
    jobs = enumerate_jobs(cfg, output_root=output_root, include_variants=True)

    selection = select_generate_jobs(jobs, retry_failed=retry_failed)
    pending = selection.pending

    provider = (
        cfg.provider_for(entry.provider_id) if entry.provider_id is not None else None
    )
    print(f"\n--- {system_name}/{entry.label} ---")
    print(f"  output_root:    {output_root}")
    print(
        "  mode:           "
        + (
            f"provider {provider.provider} / {provider.model}"
            if provider is not None
            else f"default (CLI global account, tag={entry.default_tag!r})"
        )
    )
    print(f"  jobs:           {len(jobs)}")
    print(f"  done:           {selection.done} (model.spthy present)")
    if selection.prior_failures:
        breakdown = ", ".join(
            f"{outcome}={count}"
            for outcome, count in sorted(selection.prior_failure_outcomes.items())
        )
        print(f"  prior failures: {selection.prior_failures} ({breakdown})")
    print(f"  to_run:         {len(pending)}")

    if not pending:
        return 0

    succeeded = 0
    failed: list[tuple[str, str]] = []
    with ProcessPoolExecutor(max_workers=max(1, parallel)) as pool:
        futures = {
            pool.submit(_run_one, job, cfg, tool, entry, provider): job
            for job in pending
        }
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
        f"  --- {system_name}/{entry.label}: succeeded={succeeded} "
        f"failed={len(failed)} done={selection.done} "
        f"prior_failures={selection.prior_failures} ---"
    )
    if failed:
        for label, reason in failed:
            print(f"    - {label} ({reason})")
        return 1
    return 0


def _run_one(
    job: ProtocolJob,
    cfg: ExperimentConfig,
    tool: str,
    entry: ModelEntry,
    provider: ProviderEntry | None,
) -> str:
    job.output_dir.mkdir(parents=True, exist_ok=True)
    annotation = entry.default_tag if entry.provider_id is None else entry.label
    cmd = [
        sys.executable,
        "-m",
        "scripts.coding_agent_model",
        "--document",
        str(job.document),
        "--output-dir",
        str(job.output_dir),
        "--tool",
        tool,
        "--model",
        annotation or "",
        "--skills-source",
        str(SKILLS_SOURCE),
        "--agent-timeout",
        str(cfg.task_timeout_seconds),
        "--label",
        job.label,
    ]
    if provider is not None:
        cmd += [
            "--provider",
            provider.provider,
            "--api-model",
            provider.model,
            "--api-key",
            provider.api_key,
        ]
        if provider.base_url:
            cmd += ["--base-url", provider.base_url]

    log_path = job.output_dir / "run.log"
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"$ {' '.join(cmd)}\n\n")
        log.flush()
        try:
            proc = subprocess.run(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run scripts.coding_agent_model over every document.txt under "
            "the configured dataset, with a worker pool. Generates "
            "model.spthy only; use experiments.run_score for scoring."
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
        required=True,
        choices=list(CODING_AGENT_SYSTEMS),
        help="Which coding-agent system to run",
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
            "Re-attempt jobs whose previous attempt timed out or exited "
            "non-zero (default: skip them based on gen.status)."
        ),
    )
    return parser


if __name__ == "__main__":
    sys.exit(main())
