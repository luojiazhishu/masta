from __future__ import annotations

from collections.abc import Mapping, Sequence

from src.evaluation.action_block_parser import (
    ActionBlockParseError,
    parse_action_blocks,
)
from src.evaluation.metrics import (
    align_by_similarity,
    compute_m1,
    compute_m2,
    compute_m3,
    compute_m4,
    compute_m5,
)
from src.model.action_block import ActionBlock
from src.model.protocol_prediction import ProtocolPrediction
from src.model.protocol_spec import ProtocolSpec
from src.model.score import DatasetScore, ProtocolScore


def score_protocol(
    spec: ProtocolSpec, prediction: ProtocolPrediction
) -> ProtocolScore:
    """Compute the five-dimensional score for one protocol.

    The baseline side is parsed strictly: a malformed dataset entry
    propagates the parser's error. The predicted side is parsed
    defensively via ``_safe_parse_blocks`` so an agent's bad output
    simply scores zero on the affected metrics rather than aborting.

    M3 and M5 share the ``align_by_similarity`` mapping computed once
    here: each baseline lemma is paired with the predicted lemma whose
    ``lemma_similarity`` is highest. M2 reuses the same per-baseline
    similarity scores but exposes them as a mean rather than a verdict
    lookup.
    """

    baseline_blocks = parse_action_blocks(spec.baseline_blocks_text)
    predicted_blocks = _safe_parse_blocks(prediction.base_action_blocks_text)
    m1 = compute_m1(predicted_blocks, baseline_blocks)

    name_map = align_by_similarity(
        spec.baseline_lemmas, prediction.base_lemma_definitions
    )
    m2 = compute_m2(spec.baseline_lemmas, prediction.base_lemma_definitions)
    m3 = compute_m3(
        spec.baseline_lemmas,
        name_map,
        prediction.base_lemma_verdicts,
        prediction.base_lemma_steps,
    )

    baseline_action_var: dict[str, Sequence[ActionBlock]] = {
        variant.id: parse_action_blocks(variant.baseline_blocks_text)
        for variant in spec.action_variants
    }
    predicted_action_var: dict[str, Sequence[ActionBlock] | None] = {
        variant_id: _safe_parse_blocks(text)
        for variant_id, text in prediction.action_variant_blocks_texts.items()
    }
    m4 = compute_m4(predicted_action_var, baseline_action_var)

    baseline_targets: dict[str, Sequence[str]] = {
        variant.id: list(variant.falsified_lemmas)
        for variant in spec.weakness_variants
    }
    m5 = compute_m5(
        prediction.weakness_variant_verdicts, baseline_targets, name_map
    )

    return ProtocolScore(
        protocol_id=spec.id,
        m1=m1,
        m2=m2,
        m3=m3,
        m4=m4,
        m5=m5,
    )


def score_dataset(
    specs: Sequence[ProtocolSpec],
    predictions: Mapping[str, ProtocolPrediction],
) -> DatasetScore:
    """Aggregate per-protocol scores into a dataset-level summary.

    Protocols not present in ``predictions`` are scored against an empty
    ``ProtocolPrediction`` (M1 collapses to 0 when the baseline is
    non-empty; M2/M3/M5 follow each metric's missing-input semantics
    through the empty alignment). Empty ``specs`` yield 1.0 across every
    dataset-level mean (vacuous).
    """

    scores: list[ProtocolScore] = []
    for spec in specs:
        prediction = predictions.get(spec.id) or ProtocolPrediction(
            protocol_id=spec.id
        )
        scores.append(score_protocol(spec, prediction))
    n = len(scores)
    if n == 0:
        return DatasetScore(
            protocols=(),
            mean_m1=1.0,
            mean_m2=1.0,
            mean_m3=1.0,
            mean_m4=1.0,
            mean_m5=1.0,
        )
    return DatasetScore(
        protocols=tuple(scores),
        mean_m1=sum(s.m1 for s in scores) / n,
        mean_m2=sum(s.m2 for s in scores) / n,
        mean_m3=sum(s.m3 for s in scores) / n,
        mean_m4=sum(s.m4 for s in scores) / n,
        mean_m5=sum(s.m5 for s in scores) / n,
    )


def _safe_parse_blocks(text: str) -> tuple[ActionBlock, ...]:
    if not text.strip():
        return ()
    try:
        return parse_action_blocks(text)
    except ActionBlockParseError:
        return ()
