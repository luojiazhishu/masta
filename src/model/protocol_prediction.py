from __future__ import annotations

from dataclasses import dataclass, field

from src.model.verdict import TamarinVerdict


@dataclass
class ProtocolPrediction:
    """All artefacts an agent run produces for a single protocol.

    ``base_lemma_definitions`` maps the agent's chosen lemma name to the
    full ``lemma <name>: "..."`` text. ``base_lemma_verdicts`` maps the
    same agent name to the Tamarin verdict observed in the base run. Both
    dicts preserve insertion order so callers may read them positionally.
    The baseline-to-agent name resolution is the responsibility of
    ``align_by_similarity`` in the evaluation layer.

    ``action_variant_blocks_texts`` is keyed by the variant id.
    ``weakness_variant_verdicts[w][agent_lemma_name]`` is the verdict the
    agent's run for variant ``w`` produced for ``agent_lemma_name``; the
    agent must reuse the base lemma names in every weakness variant.
    """

    protocol_id: str
    base_action_blocks_text: str = ""
    base_lemma_definitions: dict[str, str] = field(default_factory=dict)
    base_lemma_verdicts: dict[str, TamarinVerdict | None] = field(
        default_factory=dict
    )
    # Tamarin-reported proof step count per agent lemma name (the
    # integer in ``verified (N steps)``); meaningful only for verdicts
    # that came back VERIFIED. ``None`` when the run did not produce a
    # step count.
    base_lemma_steps: dict[str, int | None] = field(default_factory=dict)
    action_variant_blocks_texts: dict[str, str] = field(default_factory=dict)
    weakness_variant_verdicts: dict[str, dict[str, TamarinVerdict | None]] = field(
        default_factory=dict
    )
