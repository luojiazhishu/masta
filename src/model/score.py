from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProtocolScore:
    """Five-dimensional score for one protocol under one system."""

    protocol_id: str
    m1: float
    m2: float
    m3: float
    m4: float
    m5: float


@dataclass(frozen=True)
class DatasetScore:
    """Dataset-level aggregation: per-protocol breakdown plus arithmetic
    mean of every metric across the included protocols."""

    protocols: tuple[ProtocolScore, ...]
    mean_m1: float
    mean_m2: float
    mean_m3: float
    mean_m4: float
    mean_m5: float
