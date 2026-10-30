from __future__ import annotations

from src.model.lemma import (
    Action,
    And,
    App,
    Const,
    Equation,
    Exists,
    Forall,
    Formula,
    Implies,
    Lemma,
    Not,
    Or,
    Order,
    Sum,
    Term,
    Tuple,
    Var,
)


def alpha_equivalent(left: Formula, right: Formula) -> bool:
    """Decide alpha equivalence between two Tamarin lemma formulas.

    Bound variables may be renamed via a bijection that respects sort and
    quantifier scoping; quantifier kind (``All`` / ``Ex``) and binder
    arities must match. Function symbols, action fact names, and quoted
    constants are treated as fixed and must agree exactly. Tuple and
    multiset elements are compared positionally.
    """

    return _equal(left, right, {}, {})


def alpha_equivalent_lemmas(left: Lemma, right: Lemma) -> bool:
    """Convenience: alpha equivalence on the formula component of two lemmas."""

    return alpha_equivalent(left.formula, right.formula)


def _equal(
    left: object, right: object, fwd: dict[str, str], bwd: dict[str, str]
) -> bool:
    if type(left) is not type(right):
        return False

    if isinstance(left, Var):
        assert isinstance(right, Var)
        if left.sort != right.sort:
            return False
        bound_to = fwd.get(left.name)
        if bound_to is not None:
            return bound_to == right.name and bwd.get(right.name) == left.name
        if right.name in bwd:
            return False
        return left.name == right.name

    if isinstance(left, Const):
        assert isinstance(right, Const)
        return left.value == right.value

    if isinstance(left, App):
        assert isinstance(right, App)
        return (
            left.name == right.name
            and _all_equal(left.args, right.args, fwd, bwd)
        )

    if isinstance(left, Tuple):
        assert isinstance(right, Tuple)
        return _all_equal(left.elements, right.elements, fwd, bwd)

    if isinstance(left, Sum):
        assert isinstance(right, Sum)
        return _all_equal(left.elements, right.elements, fwd, bwd)

    if isinstance(left, (Forall, Exists)):
        assert isinstance(right, (Forall, Exists))
        if len(left.vars) != len(right.vars):
            return False
        for la, lb in zip(left.vars, right.vars):
            if la.sort != lb.sort:
                return False
        saved_fwd: dict[str, str | None] = {}
        saved_bwd: dict[str, str | None] = {}
        for la, lb in zip(left.vars, right.vars):
            saved_fwd[la.name] = fwd.get(la.name)
            saved_bwd[lb.name] = bwd.get(lb.name)
            fwd[la.name] = lb.name
            bwd[lb.name] = la.name
        try:
            return _equal(left.body, right.body, fwd, bwd)
        finally:
            for la, lb in zip(left.vars, right.vars):
                _restore(fwd, la.name, saved_fwd[la.name])
                _restore(bwd, lb.name, saved_bwd[lb.name])

    if isinstance(left, (Implies, And, Or)):
        assert isinstance(right, (Implies, And, Or))
        return (
            _equal(left.left, right.left, fwd, bwd)
            and _equal(left.right, right.right, fwd, bwd)
        )

    if isinstance(left, Not):
        assert isinstance(right, Not)
        return _equal(left.formula, right.formula, fwd, bwd)

    if isinstance(left, Action):
        assert isinstance(right, Action)
        return (
            left.name == right.name
            and _all_equal(left.args, right.args, fwd, bwd)
            and _equal(left.timepoint, right.timepoint, fwd, bwd)
        )

    if isinstance(left, (Equation, Order)):
        assert isinstance(right, (Equation, Order))
        return (
            _equal(left.left, right.left, fwd, bwd)
            and _equal(left.right, right.right, fwd, bwd)
        )

    return False


def _all_equal(
    left: tuple[Term, ...],
    right: tuple[Term, ...],
    fwd: dict[str, str],
    bwd: dict[str, str],
) -> bool:
    if len(left) != len(right):
        return False
    return all(_equal(a, b, fwd, bwd) for a, b in zip(left, right))


def _restore(env: dict[str, str], key: str, prior: str | None) -> None:
    if prior is None:
        env.pop(key, None)
    else:
        env[key] = prior
