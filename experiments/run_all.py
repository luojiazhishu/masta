"""Drive every selected experiment end-to-end against the unified config.

Two phases are orchestrated:

* ``generate`` — runs ``masta`` / ``oneshot`` / ``codex`` / ``claude``
  / ``opencode`` (whichever subset is selected) to produce
  ``model.spthy`` per (protocol|variant, run_idx).
* ``score`` — runs ``experiments.run_score`` over the selected systems
  to write ``base/score.json`` for every base ``model.spthy`` produced
  so far.

``--phase`` picks one of ``generate`` / ``score`` / ``all`` (default
``all``: generate then score). ``--experiments`` limits the systems
under consideration in either phase; defaults to ``cfg.experiments``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from experiments._common import (
    CODING_AGENT_SYSTEMS,
    DEFAULT_CONFIG_PATH,
    KNOWN_EXPERIMENTS,
    ExperimentConfigError,
    load_experiment_config,
)


load_dotenv()


PHASES = ("generate", "score", "all")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        cfg = load_experiment_config(args.config)
    except ExperimentConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    selected = (
        _parse_selection(args.experiments)
        if args.experiments
        else cfg.experiments
    )

    phases: tuple[str, ...]
    if args.phase == "all":
        phases = ("generate", "score")
    else:
        phases = (args.phase,)

    print("==== MASTA run_all ====")
    print(f" config:      {args.config}")
    print(f" experiments: {', '.join(selected)}")
    print(f" phases:      {', '.join(phases)}")

    forwarded = ["--config", str(args.config)]
    if args.parallel is not None:
        forwarded += ["--parallel", str(args.parallel)]
    if args.retry_failed:
        forwarded += ["--retry-failed"]
    if args.force:
        forwarded += ["--force"]

    results: list[tuple[str, str, int]] = []
    for phase in phases:
        if phase == "generate":
            for system in selected:
                print(f"\n>>> [generate/{system}] starting")
                rc = _dispatch_generate(system, forwarded)
                results.append((phase, system, rc))
                status = "ok" if rc == 0 else f"FAIL exit={rc}"
                print(f"<<< [generate/{system}] {status}")
        elif phase == "score":
            print(f"\n>>> [score] starting (systems: {', '.join(selected)})")
            argv_score = list(forwarded) + ["--system", ",".join(selected)]
            rc = _dispatch_score(argv_score)
            results.append((phase, ",".join(selected), rc))
            status = "ok" if rc == 0 else f"FAIL exit={rc}"
            print(f"<<< [score] {status}")
        else:
            print(f"  ! unknown phase {phase!r}", file=sys.stderr)
            results.append((phase, "-", 2))

    print("\n==== summary ====")
    failed = 0
    for phase, target, rc in results:
        marker = "ok" if rc == 0 else f"FAIL exit={rc}"
        print(f"  {phase}/{target}: {marker}")
        if rc != 0:
            failed += 1
    return 0 if failed == 0 else 1


def _dispatch_generate(system: str, forwarded: list[str]) -> int:
    if system == "masta":
        from experiments.run_masta import main as run_main
        argv = forwarded
    elif system == "oneshot":
        from experiments.run_oneshot import main as run_main
        argv = forwarded
    elif system in CODING_AGENT_SYSTEMS:
        from experiments.run_coding_agent import main as run_main
        argv = forwarded + ["--system", system]
    else:
        print(f"  ! unknown experiment {system!r}", file=sys.stderr)
        return 2
    try:
        return run_main(argv)
    except SystemExit as exc:
        return int(exc.code or 0)


def _dispatch_score(argv: list[str]) -> int:
    from experiments.run_score import main as run_main
    try:
        return run_main(argv)
    except SystemExit as exc:
        return int(exc.code or 0)


def _parse_selection(raw: str) -> tuple[str, ...]:
    items = [s.strip() for s in raw.split(",") if s.strip()]
    if not items:
        raise SystemExit("--experiments must list at least one system")
    invalid = [s for s in items if s not in KNOWN_EXPERIMENTS]
    if invalid:
        raise SystemExit(
            f"unknown experiments: {invalid}; allowed: {list(KNOWN_EXPERIMENTS)}"
        )
    seen: list[str] = []
    for s in items:
        if s not in seen:
            seen.append(s)
    return tuple(seen)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Dispatch generate / score phases over one or more experiment "
            "systems (masta / oneshot / codex / claude / opencode)."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to experiment YAML (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--experiments",
        default="",
        help=(
            "Comma-separated subset of "
            f"{','.join(KNOWN_EXPERIMENTS)}; defaults to the 'experiments' "
            "list in the config."
        ),
    )
    parser.add_argument(
        "--phase",
        choices=list(PHASES),
        default="all",
        help=(
            "Which phase(s) to run: 'generate' produces model.spthy, "
            "'score' computes per-base score.json, 'all' runs both in "
            "order. Default: all."
        ),
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=None,
        help="Override config.parallel for every dispatched runner",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help=(
            "Forward --retry-failed to every dispatched runner: re-attempt "
            "jobs whose previous attempt timed out or exited non-zero."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Forward --force to the score phase: overwrite existing score.json.",
    )
    return parser


if __name__ == "__main__":
    sys.exit(main())
