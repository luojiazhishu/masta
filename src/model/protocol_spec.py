from __future__ import annotations

from dataclasses import dataclass

from src.model.verdict import TamarinVerdict


@dataclass(frozen=True)
class BaselineLemmaSpec:
    """A single lemma declared in a protocol's baseline metadata.

    ``verified_steps`` is the Tamarin-reported proof step count for this
    lemma against the baseline ``model.spthy`` (only meaningful when
    ``outcome`` is VERIFIED). Used by M3 to scale a verdict-consistent
    lemma by ``min(experiment_steps / verified_steps, 1)``. ``None``
    when the dataset has no recorded step count yet — fall back to a
    plain verdict-consistency score in that case.
    """

    name: str
    type: str
    outcome: TamarinVerdict
    definition_text: str
    verified_steps: int | None = None


@dataclass(frozen=True)
class ActionVariantSpec:
    """An action-renaming variant; carries the variant's document and its
    baseline action_blocks.yaml content (the ground truth for M4)."""

    id: str
    description: str
    document_text: str
    baseline_blocks_text: str


@dataclass(frozen=True)
class WeaknessVariantSpec:
    """A semantic-weakening variant.

    ``falsified_lemmas`` lists the lemma names whose verdicts are expected
    to flip; ``lemma_outcomes`` carries the per-lemma baseline verdicts
    actually persisted under ``weakness_variants/<id>/<LEMMA>/outcome.txt``.
    Both are tuples (rather than dicts) to preserve the frozen / hashable
    contract of the dataclass.
    """

    id: str
    description: str
    falsified_lemmas: tuple[str, ...]
    document_text: str
    lemma_outcomes: tuple[tuple[str, TamarinVerdict], ...]


@dataclass(frozen=True)
class ProtocolSpec:
    """All ground-truth inputs the evaluation needs for one protocol."""

    id: str
    title: str
    document_text: str
    model_spec_text: str
    baseline_blocks_text: str
    baseline_lemmas: tuple[BaselineLemmaSpec, ...]
    action_variants: tuple[ActionVariantSpec, ...]
    weakness_variants: tuple[WeaknessVariantSpec, ...]
