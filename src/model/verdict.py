from __future__ import annotations

from enum import Enum


class TamarinVerdict(Enum):
    """Verification outcome a Tamarin proof returns for a single lemma.

    The string values match the ``outcome_tokens`` convention declared in
    ``data/dataset/metadata.yaml`` and are the literal contents of every
    ``outcome.txt`` file in the dataset.
    """

    VERIFIED = "verified"
    FALSIFIED = "falsified"
