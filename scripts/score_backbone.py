"""Score the three-model backbone comparison with the protocol scorer."""

from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

import yaml


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
BACKBONE_MODELS = ("qwen3.5-plus", "claude-sonnet-4-6", "gpt-5-mini")
BACKBONE_PROTOCOLS = {
    "001_cr_xor": ("03", "04", "05"),
    "003_pattern_matching_and_destructors": ("01", "03", "04"),
    "008_H1": ("01", "02", "04"),
}


def load_backbone_metadata(dataset_dir: Path) -> dict:
    """Keep the declared evaluation scope independent of missing outputs."""
    metadata = yaml.safe_load((dataset_dir / "metadata.yaml").read_text())
    if not isinstance(metadata, dict) or not isinstance(metadata.get("entries"), list):
        raise ValueError("Dataset metadata must contain an entries list")
    entries = {entry["id"]: entry for entry in metadata["entries"]}
    selected = []
    for protocol, action_ids in BACKBONE_PROTOCOLS.items():
        if protocol not in entries or not (dataset_dir / protocol).is_dir():
            raise ValueError(f"Dataset protocol missing: {protocol}")
        entry = dict(entries[protocol])
        variants = {
            str(variant["id"]).zfill(2): variant
            for variant in entry.get("action_variants", [])
        }
        missing = set(action_ids) - variants.keys()
        if missing:
            raise ValueError(f"Action variants missing for {protocol}: {sorted(missing)}")
        entry["action_variants"] = [variants[variant] for variant in action_ids]
        selected.append(entry)
    return {**metadata, "entries": selected}


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    dataset_dir = args.dataset_dir.resolve()
    runs_dir = args.runs_dir.resolve()
    try:
        metadata = load_backbone_metadata(dataset_dir)
        runs = [
            (protocol, runs_dir / model / protocol / "run_1")
            for model in args.models for protocol in BACKBONE_PROTOCOLS
        ]
        for _, run in runs:
            if not run.is_dir():
                raise ValueError(f"Run directory missing: {run}")
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    pending = [
        (protocol, run) for protocol, run in runs
        if args.force or not (run / "score.json").is_file()
    ]
    print(f"Runs: {len(runs)}; already scored: {len(runs) - len(pending)}; to score: {len(pending)}")
    if not pending:
        return 0

    with tempfile.TemporaryDirectory(prefix="masta_backbone_") as directory:
        subset = Path(directory)
        (subset / "metadata.yaml").write_text(yaml.safe_dump(metadata, sort_keys=False))
        for protocol in BACKBONE_PROTOCOLS:
            (subset / protocol).symlink_to(dataset_dir / protocol, target_is_directory=True)
        for protocol, run in pending:
            command = [
                sys.executable, "-m", "scripts.score_protocol",
                "--run-dir", str(run), "--dataset-dir", str(subset),
                "--protocol-id", protocol,
                "--tamarin-timeout", str(args.tamarin_timeout),
            ]
            if args.force:
                command.append("--force")
            log_path = run / "score.log"
            print(f"[score] {run.relative_to(runs_dir)}")
            with log_path.open("w") as log:
                log.write(f"$ {shlex.join(command)}\n\n")
                log.flush()
                result = subprocess.run(
                    command, cwd=PACKAGE_ROOT, stdout=log, stderr=subprocess.STDOUT,
                )
            if result.returncode != 0:
                print(f"ERROR: scoring failed; see {log_path}", file=sys.stderr)
                return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=PACKAGE_ROOT / "data/dataset")
    parser.add_argument("--runs-dir", type=Path, default=PACKAGE_ROOT / "runs/masta_backbone")
    parser.add_argument("--models", nargs="+", choices=BACKBONE_MODELS, default=BACKBONE_MODELS)
    parser.add_argument("--tamarin-timeout", type=int, default=300)
    parser.add_argument("--force", action="store_true", help="Recompute existing scores.")
    return parser


if __name__ == "__main__":
    sys.exit(main())
