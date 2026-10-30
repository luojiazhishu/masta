from __future__ import annotations

from collections.abc import Mapping, Sequence

from src.evaluation.lemma_alpha import alpha_equivalent
from src.evaluation.lemma_parser import LemmaParseError, parse_lemma
from src.model.lemma import Formula
from src.model.protocol_spec import BaselineLemmaSpec


def align_lemma_names(
    baseline_lemmas: Sequence[BaselineLemmaSpec],
    predicted_definitions: Mapping[str, str],
) -> dict[str, str | None]:
    """Resolve baseline lemma names to agent-chosen names.

    Two modes:

    - When the baseline and predicted lemma counts match, align by
      position and verify each pair with alpha equivalence; a pair that
      fails alpha (or whose formula fails to parse) maps the baseline
      name to ``None``.
    - When the counts differ, fall back to alpha-equivalence matching:
      each baseline lemma is paired with the first unused predicted
      lemma whose formula is alpha-equivalent; baselines without a match
      map to ``None``.

    The returned mapping always has every baseline name as a key.
    """

    baseline_pairs = [
        (lemma.name, _safe_parse_formula(lemma.definition_text))
        for lemma in baseline_lemmas
    ]
    predicted_pairs = [
        (name, _safe_parse_formula(text))
        for name, text in predicted_definitions.items()
    ]
    if len(baseline_pairs) == len(predicted_pairs):
        return _align_positional(baseline_pairs, predicted_pairs)
    return _align_by_alpha(baseline_pairs, predicted_pairs)


def _align_positional(
    baseline_pairs: Sequence[tuple[str, Formula | None]],
    predicted_pairs: Sequence[tuple[str, Formula | None]],
) -> dict[str, str | None]:
    mapping: dict[str, str | None] = {}
    for (b_name, b_formula), (p_name, p_formula) in zip(
        baseline_pairs, predicted_pairs
    ):
        if (
            b_formula is not None
            and p_formula is not None
            and alpha_equivalent(b_formula, p_formula)
        ):
            mapping[b_name] = p_name
        else:
            mapping[b_name] = None
    return mapping


def _align_by_alpha(
    baseline_pairs: Sequence[tuple[str, Formula | None]],
    predicted_pairs: Sequence[tuple[str, Formula | None]],
) -> dict[str, str | None]:
    mapping: dict[str, str | None] = {}
    used: set[str] = set()
    for b_name, b_formula in baseline_pairs:
        match: str | None = None
        if b_formula is not None:
            for p_name, p_formula in predicted_pairs:
                if p_name in used or p_formula is None:
                    continue
                if alpha_equivalent(b_formula, p_formula):
                    match = p_name
                    used.add(p_name)
                    break
        mapping[b_name] = match
    return mapping


def _safe_parse_formula(text: str) -> Formula | None:
    if not text.strip():
        return None
    try:
        return parse_lemma(text).formula
    except LemmaParseError:
        return None
