from __future__ import annotations

from src.evaluation.lemma_alpha import alpha_equivalent
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
    Not,
    Or,
    Order,
    Sum,
    Term,
    Tuple,
    Var,
)


def lemma_similarity(left: Formula, right: Formula) -> float:
    """Normalised tree-edit distance between two lemma formulas.

    Returns 1.0 when the two formulas are alpha-equivalent (Var
    re-naming, same sort), so structurally identical lemmas with
    different bound-variable spellings still score 1.0. Otherwise the
    score is ``1 - TED(left, right) / max(|left|, |right|)`` where the
    tree edit distance treats every AST node (formula or term) as a
    unit-cost edit and the node label folds in just enough information
    to distinguish kinds without leaking bound-variable names (Var
    nodes carry only their sort, App / Action carry their function /
    fact name, Const carries its value).
    """

    if alpha_equivalent(left, right):
        return 1.0
    distance = _tree_edit_distance(left, right)
    size = max(_node_count(left), _node_count(right))
    if size == 0:
        return 1.0
    similarity = 1.0 - distance / size
    if similarity < 0.0:
        return 0.0
    return similarity


def _label(node: object) -> tuple:
    """A label tuple comparable across nodes. Bound-variable names are
    intentionally absent — Var label is keyed only by sort, so two
    formulas that differ only in alpha-renaming yield equal labels at
    every Var position and the only edits TED counts come from real
    structural differences.
    """

    if isinstance(node, Var):
        return ("Var", node.sort)
    if isinstance(node, Const):
        return ("Const", node.value)
    if isinstance(node, App):
        return ("App", node.name)
    if isinstance(node, Tuple):
        return ("Tuple",)
    if isinstance(node, Sum):
        return ("Sum",)
    if isinstance(node, Forall):
        return ("Forall", len(node.vars))
    if isinstance(node, Exists):
        return ("Exists", len(node.vars))
    if isinstance(node, Implies):
        return ("Implies",)
    if isinstance(node, And):
        return ("And",)
    if isinstance(node, Or):
        return ("Or",)
    if isinstance(node, Not):
        return ("Not",)
    if isinstance(node, Action):
        return ("Action", node.name)
    if isinstance(node, Equation):
        return ("Equation",)
    if isinstance(node, Order):
        return ("Order",)
    return ("Unknown", type(node).__name__)


def _children(node: object) -> tuple:
    if isinstance(node, (Var, Const)):
        return ()
    if isinstance(node, App):
        return node.args
    if isinstance(node, Tuple):
        return node.elements
    if isinstance(node, Sum):
        return node.elements
    if isinstance(node, (Forall, Exists)):
        return tuple(node.vars) + (node.body,)
    if isinstance(node, (And, Or, Implies)):
        return (node.left, node.right)
    if isinstance(node, Not):
        return (node.formula,)
    if isinstance(node, Action):
        return tuple(node.args) + (node.timepoint,)
    if isinstance(node, (Equation, Order)):
        return (node.left, node.right)
    return ()


def _node_count(node: object) -> int:
    return 1 + sum(_node_count(c) for c in _children(node))


def _tree_edit_distance(a: object, b: object) -> int:
    """Selkow-style ordered tree edit distance with unit costs.

    The cost of replacing the root is 0 when labels agree and 1
    otherwise; aligning the children lists is itself a sequence edit
    distance whose match cost recurses into ``_tree_edit_distance``.
    Insert / delete of a whole subtree costs that subtree's node count.
    """

    label_cost = 0 if _label(a) == _label(b) else 1
    children_a = _children(a)
    children_b = _children(b)
    return label_cost + _seq_edit_distance(children_a, children_b)


def _seq_edit_distance(a: tuple, b: tuple) -> int:
    n = len(a)
    m = len(b)
    if n == 0:
        return sum(_node_count(x) for x in b)
    if m == 0:
        return sum(_node_count(x) for x in a)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] + _node_count(a[i - 1])
    for j in range(1, m + 1):
        dp[0][j] = dp[0][j - 1] + _node_count(b[j - 1])
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            delete = dp[i - 1][j] + _node_count(a[i - 1])
            insert = dp[i][j - 1] + _node_count(b[j - 1])
            replace = dp[i - 1][j - 1] + _tree_edit_distance(a[i - 1], b[j - 1])
            dp[i][j] = min(delete, insert, replace)
    return dp[n][m]
