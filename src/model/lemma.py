from __future__ import annotations

from dataclasses import dataclass


VAR_SORT_TEMPORAL = "temporal"
VAR_SORT_FRESH = "fresh"
VAR_SORT_PUBLIC = "public"
VAR_SORT_MESSAGE = "message"


@dataclass(frozen=True)
class Var:
    name: str
    sort: str


@dataclass(frozen=True)
class Const:
    value: str


@dataclass(frozen=True)
class App:
    name: str
    args: tuple[Term, ...]


@dataclass(frozen=True)
class Tuple:
    elements: tuple[Term, ...]


@dataclass(frozen=True)
class Sum:
    elements: tuple[Term, ...]


Term = Var | Const | App | Tuple | Sum


@dataclass(frozen=True)
class Forall:
    vars: tuple[Var, ...]
    body: Formula


@dataclass(frozen=True)
class Exists:
    vars: tuple[Var, ...]
    body: Formula


@dataclass(frozen=True)
class Implies:
    left: Formula
    right: Formula


@dataclass(frozen=True)
class And:
    left: Formula
    right: Formula


@dataclass(frozen=True)
class Or:
    left: Formula
    right: Formula


@dataclass(frozen=True)
class Not:
    formula: Formula


@dataclass(frozen=True)
class Action:
    name: str
    args: tuple[Term, ...]
    timepoint: Var


@dataclass(frozen=True)
class Equation:
    left: Term
    right: Term


@dataclass(frozen=True)
class Order:
    left: Term
    right: Term


Formula = Forall | Exists | Implies | And | Or | Not | Action | Equation | Order


@dataclass(frozen=True)
class Lemma:
    name: str
    options: tuple[str, ...]
    formula: Formula
