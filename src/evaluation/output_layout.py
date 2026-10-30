"""Output directory layout that mirrors the test-set protocol directory.

Every system runner writes its per-protocol artifacts under

    <runs_dir>/<system_name>/<safe_model>/<protocol_id>/run_<i>/

Final layout::

    <protocol_id>/
      model.spthy
      lemmas/<NN>_<TYPE>_<NAME>/
        definition.txt
        outcome.txt
      action_variants/<id>/
        model.spthy
        action_blocks.yaml
      weakness_variants/<id>/
        model.spthy
        <LEMMA_NAME>/outcome.txt

This module is the single source of truth for the file and directory names
that participate in that mirroring; non-mirrored intermediates (such as
agent-level review files) must NOT use these constants.
"""

from __future__ import annotations

from pathlib import Path

from src.model.verdict import TamarinVerdict

# Top-level files inside <protocol_id>/
DOCUMENT_FILENAME = "document.txt"
ACTION_BLOCKS_FILENAME = "action_blocks.yaml"
MODEL_SPEC_FILENAME = "model.spthy"

# Lemma subtree
LEMMAS_DIRNAME = "lemmas"
LEMMA_DEFINITION_FILENAME = "definition.txt"
LEMMA_OUTCOME_FILENAME = "outcome.txt"
LEMMA_STEPS_FILENAME = "steps.txt"

# Variant subtrees
ACTION_VARIANTS_DIRNAME = "action_variants"
WEAKNESS_VARIANTS_DIRNAME = "weakness_variants"


def lemma_subdir_name(index: int, lemma_type: str, lemma_name: str) -> str:
    """Format the per-lemma subdirectory name.

    The two-digit index matches the dataset's zero-padded convention. The
    full form ``<NN>_<TYPE>_<NAME>`` mirrors the dataset; when the producing
    system has no separate type metadata to record (oneshot baseline emits
    free-form lemma names whose category is recovered downstream), pass
    ``lemma_type=""`` to drop the type segment, producing ``<NN>_<NAME>``.
    """
    if index < 0:
        raise ValueError(f"lemma index must be non-negative, got {index}")
    if lemma_type:
        return f"{index:02d}_{lemma_type}_{lemma_name}"
    return f"{index:02d}_{lemma_name}"


def write_document(protocol_dir: Path, document_text: str) -> Path:
    """Write the protocol description into ``document.txt``."""
    protocol_dir.mkdir(parents=True, exist_ok=True)
    path = protocol_dir / DOCUMENT_FILENAME
    path.write_text(document_text, encoding="utf-8")
    return path


def write_model_spec(protocol_dir: Path, spec_text: str) -> Path:
    """Write the generated Tamarin spec into ``model.spthy``."""
    protocol_dir.mkdir(parents=True, exist_ok=True)
    path = protocol_dir / MODEL_SPEC_FILENAME
    path.write_text(spec_text, encoding="utf-8")
    return path


def write_lemma_outcome(
    protocol_dir: Path,
    index: int,
    lemma_type: str,
    lemma_name: str,
    definition_text: str,
    verdict: TamarinVerdict,
) -> Path:
    """Write a single lemma's ``definition.txt`` plus ``outcome.txt``.

    The verdict is serialized as the enum's lowercase string value, which is
    one of ``verified`` or ``falsified`` per project convention.
    """
    subdir = (
        protocol_dir
        / LEMMAS_DIRNAME
        / lemma_subdir_name(index, lemma_type, lemma_name)
    )
    subdir.mkdir(parents=True, exist_ok=True)
    (subdir / LEMMA_DEFINITION_FILENAME).write_text(
        definition_text, encoding="utf-8"
    )
    outcome_path = subdir / LEMMA_OUTCOME_FILENAME
    outcome_path.write_text(verdict.value, encoding="utf-8")
    return outcome_path


def write_weakness_lemma_outcome(
    weakness_variant_dir: Path,
    lemma_name: str,
    verdict: TamarinVerdict,
) -> Path:
    """Write a weakness variant's per-lemma outcome.

    The dataset layout for weakness variants is
    ``weakness_variants/<id>/<LEMMA_NAME>/outcome.txt``; there is no
    ``<NN>_<TYPE>_<NAME>`` prefix because the affected lemma names are
    enumerated directly in the metadata's ``falsified_lemmas``.
    """
    subdir = weakness_variant_dir / lemma_name
    subdir.mkdir(parents=True, exist_ok=True)
    outcome_path = subdir / LEMMA_OUTCOME_FILENAME
    outcome_path.write_text(verdict.value, encoding="utf-8")
    return outcome_path
