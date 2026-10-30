from __future__ import annotations

from dataclasses import dataclass

from src.model.verdict import TamarinVerdict


@dataclass(frozen=True)
class TamarinResult:
    """Outcome of a single Tamarin invocation against one lemma.

    ``verdict`` is the proof / falsification result. ``lemma_name`` is
    ``None`` only when the run was a wellformedness check rather than a
    proof attempt. ``stdout`` and ``stderr`` capture the raw text the
    underlying client surfaced. ``wall_time_seconds`` measures total
    elapsed time including client setup. ``raw_trace`` is the
    serialized proof path when available, otherwise ``None``. ``steps``
    is the integer Tamarin reports inside ``(N steps)`` on the per-lemma
    summary line, used by the M3 trace-length scorer; ``None`` when the
    run did not produce a proof / steps line (timeouts, parse errors,
    wellformedness checks).
    """

    verdict: TamarinVerdict
    lemma_name: str | None
    stdout: str
    stderr: str
    wall_time_seconds: float
    raw_trace: str | None
    steps: int | None = None
