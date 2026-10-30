"""Generate backbone models using the existing single-document generator."""

from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import subprocess
import sys

import yaml

from scripts.score_backbone import (
    BACKBONE_MODELS,
    PACKAGE_ROOT,
    load_backbone_metadata,
)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    dataset_dir = args.dataset_dir.resolve()
    output_dir = args.output_dir.resolve()
    try:
        metadata = load_backbone_metadata(dataset_dir)
        jobs = []
        for model in args.models:
            for entry in metadata["entries"]:
                protocol = entry["id"]
                documents = [(Path("document.txt"), Path("base"))]
                for kind in ("action_variants", "weakness_variants"):
                    for variant in entry.get(kind, []):
                        relative = Path(kind) / str(variant["id"]).zfill(2)
                        documents.append((relative / "document.txt", relative))
                for document, relative in documents:
                    source = dataset_dir / protocol / document
                    if not source.is_file():
                        raise ValueError(f"Input document missing: {source}")
                    target = output_dir / model / protocol / "run_1" / relative
                    jobs.append((model, source, target))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    generated = 0
    skipped = 0
    for model, document, target in jobs:
        model_path = target / "model.spthy"
        if model_path.is_file() and model_path.read_text().strip():
            skipped += 1
            continue
        target.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable, "-m", "scripts.masta_model",
            "--document", str(document), "--output-dir", str(target),
            "--provider", args.provider, "--model", model,
            "--tamarin-timeout", str(args.tamarin_timeout),
        ]
        log_path = target / "run.log"
        print(f"[generate] {target.relative_to(output_dir)}")
        with log_path.open("w") as log:
            log.write(f"$ {shlex.join(command)}\n\n")
            log.flush()
            result = subprocess.run(
                command, cwd=PACKAGE_ROOT, stdout=log, stderr=subprocess.STDOUT,
            )
        if result.returncode != 0:
            print(f"ERROR: generation failed; see {log_path}", file=sys.stderr)
            return 1
        generated += 1
    print(f"Generated: {generated}; already present: {skipped}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=PACKAGE_ROOT / "data/dataset")
    parser.add_argument("--output-dir", type=Path, default=PACKAGE_ROOT / "runs/masta_backbone_generated")
    parser.add_argument("--models", nargs="+", choices=BACKBONE_MODELS, default=BACKBONE_MODELS)
    parser.add_argument("--provider", choices=("openai", "anthropic"), default="openai")
    parser.add_argument("--tamarin-timeout", type=int, default=300)
    return parser


if __name__ == "__main__":
    sys.exit(main())
