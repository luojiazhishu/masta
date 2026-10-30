from __future__ import annotations

from pathlib import Path

import yaml

from src.evaluation.output_layout import (
    ACTION_BLOCKS_FILENAME,
    ACTION_VARIANTS_DIRNAME,
    DOCUMENT_FILENAME,
    LEMMA_DEFINITION_FILENAME,
    LEMMA_OUTCOME_FILENAME,
    LEMMA_STEPS_FILENAME,
    LEMMAS_DIRNAME,
    MODEL_SPEC_FILENAME,
    WEAKNESS_VARIANTS_DIRNAME,
    lemma_subdir_name,
)
from src.model.protocol_spec import (
    ActionVariantSpec,
    BaselineLemmaSpec,
    ProtocolSpec,
    WeaknessVariantSpec,
)
from src.model.verdict import TamarinVerdict


METADATA_FILENAME = "metadata.yaml"


class DatasetLoadError(ValueError):
    """Raised when the dataset directory does not match the expected layout."""


def load_dataset(dataset_dir: Path) -> tuple[ProtocolSpec, ...]:
    """Read ``metadata.yaml`` and assemble every entry into a ``ProtocolSpec``.

    Protocols are returned in the order they appear under ``entries``.
    ``DatasetLoadError`` is raised if the metadata file or any per-protocol
    file referenced from it is missing or malformed.
    """

    metadata_path = dataset_dir / METADATA_FILENAME
    if not metadata_path.is_file():
        raise DatasetLoadError(
            f"dataset metadata not found: {metadata_path}"
        )
    metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise DatasetLoadError("metadata.yaml top-level must be a mapping")
    entries = metadata.get("entries")
    if not isinstance(entries, list):
        raise DatasetLoadError("metadata.yaml must declare 'entries' as a list")
    return tuple(_load_protocol(dataset_dir, entry) for entry in entries)


def _load_protocol(dataset_dir: Path, entry: object) -> ProtocolSpec:
    if not isinstance(entry, dict):
        raise DatasetLoadError(f"protocol entry must be a mapping, got {type(entry).__name__}")
    protocol_id = _require_str(entry, "id", "<entry>")
    location = f"protocol {protocol_id!r}"
    title = _require_str(entry, "title", location)
    protocol_dir = dataset_dir / protocol_id
    if not protocol_dir.is_dir():
        raise DatasetLoadError(f"{location} directory missing: {protocol_dir}")

    document_text = _read_text(protocol_dir / DOCUMENT_FILENAME, location)
    model_spec_text = _read_text(protocol_dir / MODEL_SPEC_FILENAME, location)
    baseline_blocks_text = _read_text(
        protocol_dir / ACTION_BLOCKS_FILENAME, location
    )
    baseline_lemmas = _load_baseline_lemmas(protocol_dir, entry, location)
    action_variants = _load_action_variants(protocol_dir, entry, location)
    weakness_variants = _load_weakness_variants(
        protocol_dir, entry, location, baseline_lemmas
    )

    return ProtocolSpec(
        id=protocol_id,
        title=title,
        document_text=document_text,
        model_spec_text=model_spec_text,
        baseline_blocks_text=baseline_blocks_text,
        baseline_lemmas=baseline_lemmas,
        action_variants=action_variants,
        weakness_variants=weakness_variants,
    )


def _load_baseline_lemmas(
    protocol_dir: Path, entry: dict, location: str
) -> tuple[BaselineLemmaSpec, ...]:
    raw_lemmas = entry.get("lemmas", [])
    if not isinstance(raw_lemmas, list):
        raise DatasetLoadError(f"{location} 'lemmas' must be a list")
    lemmas: list[BaselineLemmaSpec] = []
    for index, raw in enumerate(raw_lemmas, start=1):
        if not isinstance(raw, dict):
            raise DatasetLoadError(
                f"{location} lemma #{index} must be a mapping"
            )
        name = _require_str(raw, "name", f"{location} lemma #{index}")
        lemma_type = _require_str(raw, "type", f"{location} lemma {name!r}")
        subdir = protocol_dir / LEMMAS_DIRNAME / lemma_subdir_name(
            index, lemma_type, name
        )
        definition_text = _read_text(
            subdir / LEMMA_DEFINITION_FILENAME, f"{location} lemma {name!r}"
        )
        outcome = _read_verdict(
            subdir / LEMMA_OUTCOME_FILENAME, f"{location} lemma {name!r}"
        )
        verified_steps = _read_optional_int(
            subdir / LEMMA_STEPS_FILENAME,
            f"{location} lemma {name!r}",
        )
        lemmas.append(
            BaselineLemmaSpec(
                name=name,
                type=lemma_type,
                outcome=outcome,
                definition_text=definition_text,
                verified_steps=verified_steps,
            )
        )
    return tuple(lemmas)


def _read_optional_int(path: Path, location: str) -> int | None:
    """Read an integer from ``path``; return ``None`` when the file is
    missing. Raises :class:`DatasetLoadError` on a present-but-malformed
    file (the caller knows what file it pointed at).
    """
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise DatasetLoadError(
            f"{location} expected an integer in {path.name}, got {text!r}"
        ) from exc


def _load_action_variants(
    protocol_dir: Path, entry: dict, location: str
) -> tuple[ActionVariantSpec, ...]:
    raw_variants = entry.get("action_variants", [])
    if not isinstance(raw_variants, list):
        raise DatasetLoadError(f"{location} 'action_variants' must be a list")
    variants: list[ActionVariantSpec] = []
    for raw in raw_variants:
        if not isinstance(raw, dict):
            raise DatasetLoadError(
                f"{location} action_variant entries must be mappings"
            )
        variant_id = _require_str(raw, "id", f"{location} action_variant")
        description = _require_str(
            raw, "description", f"{location} action_variant {variant_id!r}"
        )
        variant_dir = protocol_dir / ACTION_VARIANTS_DIRNAME / variant_id
        document_text = _read_text(
            variant_dir / DOCUMENT_FILENAME,
            f"{location} action_variant {variant_id!r}",
        )
        baseline_blocks_text = _read_text(
            variant_dir / ACTION_BLOCKS_FILENAME,
            f"{location} action_variant {variant_id!r}",
        )
        variants.append(
            ActionVariantSpec(
                id=variant_id,
                description=description,
                document_text=document_text,
                baseline_blocks_text=baseline_blocks_text,
            )
        )
    return tuple(variants)


def _load_weakness_variants(
    protocol_dir: Path,
    entry: dict,
    location: str,
    baseline_lemmas: tuple[BaselineLemmaSpec, ...],
) -> tuple[WeaknessVariantSpec, ...]:
    raw_variants = entry.get("weakness_variants", [])
    if not isinstance(raw_variants, list):
        raise DatasetLoadError(f"{location} 'weakness_variants' must be a list")
    subdir_to_bare = {
        lemma_subdir_name(idx, lemma.type, lemma.name): lemma.name
        for idx, lemma in enumerate(baseline_lemmas, start=1)
    }
    variants: list[WeaknessVariantSpec] = []
    for raw in raw_variants:
        if not isinstance(raw, dict):
            raise DatasetLoadError(
                f"{location} weakness_variant entries must be mappings"
            )
        variant_id = _require_str(raw, "id", f"{location} weakness_variant")
        description = _require_str(
            raw, "description", f"{location} weakness_variant {variant_id!r}"
        )
        falsified_raw = raw.get("falsified_lemmas", [])
        if not isinstance(falsified_raw, list) or not all(
            isinstance(item, str) for item in falsified_raw
        ):
            raise DatasetLoadError(
                f"{location} weakness_variant {variant_id!r} 'falsified_lemmas' "
                "must be a list of strings"
            )
        variant_dir = protocol_dir / WEAKNESS_VARIANTS_DIRNAME / variant_id
        document_text = _read_text(
            variant_dir / DOCUMENT_FILENAME,
            f"{location} weakness_variant {variant_id!r}",
        )
        outcomes: list[tuple[str, TamarinVerdict]] = []
        normalized_names: list[str] = []
        for raw_name in falsified_raw:
            outcome_path = variant_dir / raw_name / LEMMA_OUTCOME_FILENAME
            verdict = _read_verdict(
                outcome_path,
                f"{location} weakness_variant {variant_id!r} lemma {raw_name!r}",
            )
            bare_name = subdir_to_bare.get(raw_name, raw_name)
            outcomes.append((bare_name, verdict))
            normalized_names.append(bare_name)
        variants.append(
            WeaknessVariantSpec(
                id=variant_id,
                description=description,
                falsified_lemmas=tuple(normalized_names),
                document_text=document_text,
                lemma_outcomes=tuple(outcomes),
            )
        )
    return tuple(variants)


def _require_str(mapping: dict, key: str, location: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DatasetLoadError(
            f"{location} '{key}' must be a non-empty string, got {value!r}"
        )
    return value


def _read_text(path: Path, location: str) -> str:
    if not path.is_file():
        raise DatasetLoadError(f"{location} missing file: {path}")
    return path.read_text(encoding="utf-8")


def _read_verdict(path: Path, location: str) -> TamarinVerdict:
    text = _read_text(path, location).strip()
    try:
        return TamarinVerdict(text)
    except ValueError as exc:
        raise DatasetLoadError(
            f"{location} verdict {text!r} is not a known TamarinVerdict"
        ) from exc
