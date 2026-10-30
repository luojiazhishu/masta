from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Receive:
    items: tuple[str, ...]


@dataclass(frozen=True)
class GenerateNonces:
    nonces: tuple[str, ...]


@dataclass(frozen=True)
class Compute:
    equations: tuple[str, ...]


@dataclass(frozen=True)
class Assertion:
    equations: tuple[str, ...]


@dataclass(frozen=True)
class Send:
    items: tuple[str, ...]
    end: bool = False


Operation = Receive | GenerateNonces | Compute | Assertion | Send


@dataclass(frozen=True)
class ActionBlock:
    role: str
    operations: tuple[Operation, ...]
