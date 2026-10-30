from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from src.evaluation.lemma_parser import LemmaParseError, parse_lemma
from src.evaluation.lemma_similarity import lemma_similarity
from src.model.action_block import (
    ActionBlock,
    Assertion,
    Compute,
    GenerateNonces,
    Receive,
    Send,
)
from src.model.lemma import Formula
from src.model.protocol_spec import BaselineLemmaSpec
from src.model.verdict import TamarinVerdict


_TOKEN_RE = re.compile(
    r"""
        '[^']*'                                          # quoted constant
      | [~$]?[A-Za-z_][A-Za-z0-9_]*(?::[a-z]+)?          # identifier
      | [(),<>\[\]^+=]                                   # punctuation
      | ⊕                                                # XOR symbol
      | \S                                               # fallback
    """,
    re.VERBOSE,
)

_RESERVED_WORDS = frozenset({"true", "false"})
_XOR_TOKENS = frozenset({"⊕", "XOR"})


@dataclass(frozen=True)
class _Term:
    kind: str
    label: str
    sort: tuple[str, str]
    children: tuple["_Term", ...]


def _tokenize(text: str) -> list[str]:
    return [m.group(0) for m in _TOKEN_RE.finditer(text)]


def _identifier_sort(token: str) -> tuple[str, str]:
    body = token
    prefix = ""
    if body and body[0] in {"~", "$"}:
        prefix = body[0]
        body = body[1:]
    suffix = ""
    if ":" in body:
        _, _, suffix = body.partition(":")
    return (prefix, suffix)


def _looks_like_identifier(token: str) -> bool:
    if not token:
        return False
    head = token[0]
    if head in {"~", "$"}:
        if len(token) < 2:
            return False
        head = token[1]
    return head.isalpha() or head == "_"


class _TermParser:
    def __init__(self, tokens: Sequence[str]) -> None:
        self.tokens = tokens
        self.pos = 0

    def _peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _take(self) -> str:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse(self) -> _Term:
        return self._parse_xor()

    def _parse_xor(self) -> _Term:
        first = self._parse_atom()
        operands = [first]
        while self._peek() in _XOR_TOKENS:
            self._take()
            operands.append(self._parse_atom())
        if len(operands) == 1:
            return first
        return _Term(
            kind="xor", label="⊕", sort=("", ""), children=tuple(operands)
        )

    def _parse_atom(self) -> _Term:
        tok = self._peek()
        if tok is None:
            return _Term(kind="opaque", label="", sort=("", ""), children=())
        if tok == "<":
            return self._parse_tuple()
        if tok.startswith("'"):
            self._take()
            return _Term(kind="quoted", label=tok, sort=("", ""), children=())
        if tok in _RESERVED_WORDS:
            self._take()
            return _Term(
                kind="reserved", label=tok, sort=("", ""), children=()
            )
        if _looks_like_identifier(tok):
            self._take()
            if self._peek() == "(":
                self._take()
                args: list[_Term] = []
                if self._peek() != ")":
                    args.append(self._parse_xor())
                    while self._peek() == ",":
                        self._take()
                        args.append(self._parse_xor())
                if self._peek() == ")":
                    self._take()
                return _Term(
                    kind="app",
                    label=tok,
                    sort=("", ""),
                    children=tuple(args),
                )
            return _Term(
                kind="identifier",
                label=tok,
                sort=_identifier_sort(tok),
                children=(),
            )
        self._take()
        return _Term(kind="opaque", label=tok, sort=("", ""), children=())

    def _parse_tuple(self) -> _Term:
        self._take()
        items: list[_Term] = []
        if self._peek() != ">":
            items.append(self._parse_xor())
            while self._peek() == ",":
                self._take()
                items.append(self._parse_xor())
        if self._peek() == ">":
            self._take()
        return _Term(
            kind="tuple", label="", sort=("", ""), children=tuple(items)
        )


def _parse_term(text: str) -> _Term:
    return _TermParser(_tokenize(text)).parse()


def _parse_equation(text: str) -> tuple[_Term, _Term] | None:
    if "=" not in text:
        return None
    lhs_text, _, rhs_text = text.partition("=")
    return (_parse_term(lhs_text), _parse_term(rhs_text))


def _is_bare_var(term: _Term) -> bool:
    """A bare-variable identifier: no ``~``/``$`` prefix and no ``:sort`` suffix.

    These act as wildcards / pattern variables in the alpha matcher.
    """

    return term.kind == "identifier" and term.sort == ("", "")


def _rewrite_once(term: _Term, rules: Sequence[tuple[_Term, _Term]]) -> _Term:
    """Single top-down pass: try each rule at the current root first; if
    none fires, recurse into children. Outer rules win over inner ones,
    so a compute equation ``X = senc(n, k)`` collapses ``senc(n, k)``
    even when another rule would also rewrite ``n`` on its own.
    """

    for pattern, replacement in rules:
        if term == pattern:
            return replacement
    if not term.children:
        return term
    new_children = tuple(_rewrite_once(c, rules) for c in term.children)
    if new_children == term.children:
        return term
    return _Term(
        kind=term.kind,
        label=term.label,
        sort=term.sort,
        children=new_children,
    )


_REWRITE_MAX_ROUNDS = 256


def _rewrite(term: _Term, rules: Sequence[tuple[_Term, _Term]]) -> _Term:
    """Apply ``rules`` to ``term`` to a fixpoint. ``rules`` are
    ``(rhs_pattern, lhs_var)`` pairs collected from compute equations and
    prev-send / current-recv pairings (see ``_collect_substitutions``).
    Pattern matching is literal subtree equality, not alpha equivalence.

    Adversarial / pathological rule sets can form cycles (e.g. the
    agent-emitted ``compute m1 = m2`` paired with ``compute m2 = m1``)
    that never reach a fixpoint. Two safeguards:

    1. ``seen`` snapshots every term we have already passed through;
       revisiting one means we're in a cycle, so we stop and return the
       current term (any pick from the cycle is equally valid for our
       comparison purposes).
    2. ``_REWRITE_MAX_ROUNDS`` hard-caps the loop at 256 iterations as a
       last-resort timeout; in practice the cycle check trips long
       before this is reached on real inputs.
    """

    seen: set[_Term] = {term}
    for _ in range(_REWRITE_MAX_ROUNDS):
        new_term = _rewrite_once(term, rules)
        if new_term == term:
            return term
        if new_term in seen:
            return term
        seen.add(new_term)
        term = new_term
    return term


@dataclass(frozen=True)
class _PreparedBlock:
    receive: tuple[_Term, ...]
    generate: tuple[_Term, ...]
    assertion: tuple[tuple[_Term, _Term], ...]
    send: tuple[_Term, ...]
    send_end: bool


def _collect_substitutions(
    blocks: Sequence[ActionBlock],
) -> list[tuple[_Term, _Term]]:
    """Build the protocol-level rewrite rule set.

    Two sources contribute, in block order:

    * Each ``compute`` equation ``lhs = rhs`` whose ``lhs`` is a bare
      variable adds the rule ``(rhs, lhs)`` — RHS expression collapses
      back to the LHS symbol.
    * Each block's ``receive`` items are paired by index with the **direct
      predecessor block**'s ``send`` items (extra items on either side are
      dropped). When the receive side is a bare variable, the pair adds
      the rule ``(prev_send_i, recv_i)``.

    Rules are returned in the order they were discovered; ``_rewrite``
    iterates to a fixpoint, so ordering only affects ties.
    """

    rules: list[tuple[_Term, _Term]] = []
    prev_send: tuple[_Term, ...] = ()
    for block in blocks:
        for op in block.operations:
            if isinstance(op, Compute):
                for eq_text in op.equations:
                    parsed = _parse_equation(eq_text)
                    if parsed is None:
                        continue
                    lhs, rhs = parsed
                    if _is_bare_var(lhs):
                        rules.append((rhs, lhs))
        recv_items: list[_Term] = []
        for op in block.operations:
            if isinstance(op, Receive):
                recv_items.extend(_parse_term(item) for item in op.items)
        if prev_send:
            for i, recv_term in enumerate(recv_items):
                if i >= len(prev_send):
                    break
                if _is_bare_var(recv_term):
                    rules.append((prev_send[i], recv_term))
        send_items: list[_Term] = []
        for op in block.operations:
            if isinstance(op, Send):
                send_items.extend(_parse_term(item) for item in op.items)
        prev_send = tuple(send_items)
    return rules


def _prepare_protocol(
    blocks: Sequence[ActionBlock],
) -> tuple[_PreparedBlock, ...]:
    """Apply the protocol-level rewrite to every term in every block.

    The ``compute`` bucket is dropped from the prepared output (per the
    M1 spec: compute is a substitution source, not a comparison target).
    Roles are not preserved — block equality ignores them entirely.
    """

    rules = _collect_substitutions(blocks)
    prepared: list[_PreparedBlock] = []
    for block in blocks:
        receive: list[_Term] = []
        generate: list[_Term] = []
        assertion: list[tuple[_Term, _Term]] = []
        send: list[_Term] = []
        send_end = False
        for op in block.operations:
            if isinstance(op, Receive):
                for item in op.items:
                    receive.append(_rewrite(_parse_term(item), rules))
            elif isinstance(op, GenerateNonces):
                for nonce in op.nonces:
                    generate.append(_rewrite(_parse_term(nonce), rules))
            elif isinstance(op, Compute):
                continue
            elif isinstance(op, Assertion):
                for eq_text in op.equations:
                    parsed = _parse_equation(eq_text)
                    if parsed is None:
                        continue
                    lhs, rhs = parsed
                    assertion.append(
                        (_rewrite(lhs, rules), _rewrite(rhs, rules))
                    )
            elif isinstance(op, Send):
                for item in op.items:
                    send.append(_rewrite(_parse_term(item), rules))
                if op.end:
                    send_end = True
        prepared.append(
            _PreparedBlock(
                receive=tuple(receive),
                generate=tuple(generate),
                assertion=tuple(assertion),
                send=tuple(send),
                send_end=send_end,
            )
        )
    return tuple(prepared)


def _alpha_eq(
    a: _Term,
    b: _Term,
    fwd: dict[str, _Term],
    bwd: dict[str, _Term],
) -> bool:
    """Whole-tree alpha equivalence with bare-var wildcards.

    ``fwd`` maps a-side bare-var labels to the b-side term they were
    bound to; ``bwd`` is the symmetric map (b-side bare-var → a-side
    term). Same-sort prefixed identifiers are also alpha-renamable but
    only against each other, never against a bare-var on one side and a
    structured term on the other (the structured term path goes through
    bare-var wildcarding).
    """

    if _is_bare_var(a):
        prev = fwd.get(a.label)
        if prev is not None:
            return prev == b
        if _is_bare_var(b):
            prev_b = bwd.get(b.label)
            if prev_b is not None:
                return prev_b == a
            fwd[a.label] = b
            bwd[b.label] = a
            return True
        fwd[a.label] = b
        return True
    if _is_bare_var(b):
        prev = bwd.get(b.label)
        if prev is not None:
            return prev == a
        bwd[b.label] = a
        return True
    if a.kind != b.kind:
        return False
    if a.kind == "identifier":
        if a.sort != b.sort:
            return False
        prev_fwd = fwd.get(a.label)
        prev_bwd = bwd.get(b.label)
        if prev_fwd is None and prev_bwd is None:
            fwd[a.label] = b
            bwd[b.label] = a
            return True
        return prev_fwd == b and prev_bwd == a
    if a.kind in ("quoted", "reserved", "opaque"):
        return a.label == b.label
    if a.kind == "app" and a.label != b.label:
        return False
    if len(a.children) != len(b.children):
        return False
    for ac, bc in zip(a.children, b.children):
        if not _alpha_eq(ac, bc, fwd, bwd):
            return False
    return True


def _try_alpha(
    a: _Term,
    b: _Term,
    fwd: dict[str, _Term],
    bwd: dict[str, _Term],
) -> bool:
    """Run ``_alpha_eq`` on a snapshot of the bindings; commit them iff
    the match succeeds. Used by the subtree matcher and by the assert
    symmetric comparison so a failed branch leaves no residual bindings.
    """

    fwd_copy = dict(fwd)
    bwd_copy = dict(bwd)
    if _alpha_eq(a, b, fwd_copy, bwd_copy):
        fwd.clear()
        fwd.update(fwd_copy)
        bwd.clear()
        bwd.update(bwd_copy)
        return True
    return False


def _subtree_at_target(
    pattern: _Term,
    target: _Term,
    fwd: dict[str, _Term],
    bwd: dict[str, _Term],
) -> bool:
    """Search ``target`` (and its descendants, except bare-var leaves)
    for a subtree alpha-equivalent to ``pattern``.

    Bare-var leaves are skipped at the root-of-subtree position because
    a single bare-var would otherwise wildcard-absorb the entire
    ``pattern`` and let ``f(x,x)`` match ``f(senc(a), aenc(b))`` via the
    leaf ``a``. Bare-var wildcarding is still active *inside*
    ``_alpha_eq`` so internal positions like ``f(x, …)`` against
    ``f(senc(a), …)`` keep working.
    """

    if not _is_bare_var(target) and _try_alpha(pattern, target, fwd, bwd):
        return True
    for child in target.children:
        if _subtree_at_target(pattern, child, fwd, bwd):
            return True
    return False


def _subtree_at_pattern(
    pattern_root: _Term,
    target: _Term,
    fwd: dict[str, _Term],
    bwd: dict[str, _Term],
) -> bool:
    """Symmetric walk over ``pattern_root``'s subtrees against
    ``target`` as the b-side. Same bare-var-leaf skip as
    ``_subtree_at_target``: the candidate subtree must not itself be a
    bare-var, otherwise it would wildcard-absorb ``target``.
    """

    if not _is_bare_var(pattern_root) and _try_alpha(
        pattern_root, target, fwd, bwd
    ):
        return True
    for child in pattern_root.children:
        if _subtree_at_pattern(child, target, fwd, bwd):
            return True
    return False


def _term_match(
    a: _Term,
    b: _Term,
    fwd: dict[str, _Term],
    bwd: dict[str, _Term],
) -> bool:
    """Bidirectional subtree-relaxed alpha match.

    ``a ≈ b`` iff one of: (i) ``a`` and ``b`` are whole-tree alpha
    equivalent (covers bare-var wildcard at the root); (ii) ``a`` is
    alpha-equivalent to some non-bare subtree of ``b``; (iii) ``b`` is
    alpha-equivalent to some non-bare subtree of ``a``. ``fwd``/``bwd``
    are shared across the call so the caller (e.g. the assert symmetric
    comparison) can chain two ``_term_match`` invocations and expect
    Q14 binding-consistency.
    """

    if _try_alpha(a, b, fwd, bwd):
        return True
    if _is_bare_var(a) or _is_bare_var(b):
        # Whole-tree alpha already exhausted the bare-var wildcard at the
        # outer level; subtree relaxation here would only re-introduce
        # the same wildcard against deeper terms with no extra information.
        return False
    if _subtree_at_target(a, b, fwd, bwd):
        return True
    if _subtree_at_pattern(a, b, fwd, bwd):
        return True
    return False


@dataclass(frozen=True)
class _Atom:
    kind: str
    term: _Term | None
    term2: _Term | None = None


def _flatten(prepared: Sequence[_PreparedBlock]) -> tuple[_Atom, ...]:
    """Flatten the prepared protocol into a single atomic-action sequence.

    Per Q3, the per-block bucket order is
    ``receive → generate_nonces → assert → send`` and ``compute`` is
    excluded. The ``send_end`` flag is materialised as a trailing
    ``send_end`` atom inside the same block, so it must align with
    another ``send_end`` atom on the other side to count as equal.
    """

    atoms: list[_Atom] = []
    for block in prepared:
        for term in block.receive:
            atoms.append(_Atom(kind="recv", term=term))
        for term in block.generate:
            atoms.append(_Atom(kind="gen", term=term))
        for lhs, rhs in block.assertion:
            atoms.append(_Atom(kind="assert", term=lhs, term2=rhs))
        for term in block.send:
            atoms.append(_Atom(kind="send", term=term))
        if block.send_end:
            atoms.append(_Atom(kind="send_end", term=None))
    return tuple(atoms)


def _atom_equal(a: _Atom, b: _Atom) -> bool:
    """Atom-level equivalence used by the Levenshtein DP.

    ``send_end`` is a tagged marker with no payload. ``recv`` / ``gen``
    / ``send`` carry one term and use bidirectional subtree matching.
    ``assert`` carries two terms and uses the symmetric pair comparison
    from Q8.B (``(lhs≈lhs ∧ rhs≈rhs) ∨ (lhs≈rhs ∧ rhs≈lhs)``); the two
    ``_term_match`` calls inside one branch share ``fwd`` / ``bwd`` so
    binding consistency (Q14) holds within a single atom comparison.
    """

    if a.kind != b.kind:
        return False
    if a.kind == "send_end":
        return True
    if a.kind == "assert":
        assert a.term is not None and a.term2 is not None
        assert b.term is not None and b.term2 is not None
        for lhs2, rhs2 in ((b.term, b.term2), (b.term2, b.term)):
            fwd: dict[str, _Term] = {}
            bwd: dict[str, _Term] = {}
            if _term_match(a.term, lhs2, fwd, bwd) and _term_match(
                a.term2, rhs2, fwd, bwd
            ):
                return True
        return False
    assert a.term is not None and b.term is not None
    return _term_match(a.term, b.term, {}, {})


def _edit_distance(a: Sequence[_Atom], b: Sequence[_Atom]) -> int:
    """Standard Levenshtein with unit insert/delete/substitute costs.

    Equality between atoms is decided by ``_atom_equal`` — no partial
    credit, no per-cell binding propagation; ``_term_match`` already
    encapsulates the full alpha + subtree relaxation per atom.
    """

    n = len(a)
    m = len(b)
    if n == 0:
        return m
    if m == 0:
        return n
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        curr = [i] + [0] * m
        for j in range(1, m + 1):
            ins = curr[j - 1] + 1
            delete = prev[j] + 1
            sub_cost = 0 if _atom_equal(a[i - 1], b[j - 1]) else 1
            sub = prev[j - 1] + sub_cost
            curr[j] = min(ins, delete, sub)
        prev = curr
    return prev[m]


def compute_m1(
    predicted: Sequence[ActionBlock], baseline: Sequence[ActionBlock]
) -> float:
    """``M1 = 1 - ED(A, B) / max(|A|, |B|)`` over flattened atomic actions.

    Both sides go through ``_prepare_protocol`` independently — each
    side collects its own substitution rules from compute equations and
    prev-send / current-recv pairings, applies them to a fixpoint, and
    drops the compute bucket. The result is flattened into an atomic
    sequence whose ordering is fixed by Q3 (``recv → gen → assert →
    send``, plus a trailing ``send_end`` marker) and edit distance is
    computed under unit costs. Atom equality uses bidirectional subtree
    alpha matching with bare identifiers acting as wildcards.
    """

    pred_atoms = _flatten(_prepare_protocol(predicted))
    base_atoms = _flatten(_prepare_protocol(baseline))
    n = len(pred_atoms)
    m = len(base_atoms)
    if n == 0 and m == 0:
        return 1.0
    if n == 0 or m == 0:
        return 0.0
    distance = _edit_distance(pred_atoms, base_atoms)
    return 1.0 - distance / max(n, m)


def _parse_formula(text: str) -> Formula | None:
    if not text.strip():
        return None
    try:
        return parse_lemma(text).formula
    except LemmaParseError:
        return None


def _baseline_best_match(
    baseline_lemmas: Sequence[BaselineLemmaSpec],
    predicted_definitions: Mapping[str, str],
) -> list[tuple[str | None, float]]:
    """Per-baseline ``(best_predicted_name, best_similarity)``.

    For each baseline lemma the predicted list is scanned in insertion
    order; the predicted lemma with the highest ``lemma_similarity``
    wins (first-seen breaks ties). Baselines whose own formula fails
    to parse, or runs where every predicted lemma fails to parse, get
    ``(None, 0.0)``. Output is positionally aligned with
    ``baseline_lemmas``.
    """

    predicted_pairs = [
        (name, _parse_formula(text))
        for name, text in predicted_definitions.items()
    ]
    out: list[tuple[str | None, float]] = []
    for lemma in baseline_lemmas:
        b_formula = _parse_formula(lemma.definition_text)
        if b_formula is None:
            out.append((None, 0.0))
            continue
        best_name: str | None = None
        best_sim = -1.0
        for p_name, p_formula in predicted_pairs:
            if p_formula is None:
                continue
            sim = lemma_similarity(b_formula, p_formula)
            if sim > best_sim:
                best_sim = sim
                best_name = p_name
        if best_name is None:
            out.append((None, 0.0))
        else:
            out.append((best_name, max(0.0, best_sim)))
    return out


def align_by_similarity(
    baseline_lemmas: Sequence[BaselineLemmaSpec],
    predicted_definitions: Mapping[str, str],
) -> dict[str, str | None]:
    """Map each baseline lemma name to the predicted name with highest
    similarity (or ``None`` if no predicted candidate is parsable).

    Used by M3 / M5 so all three lemma-side metrics share one match
    decision. M2 doesn't go through this because it needs the numeric
    score, not just the chosen agent name.
    """

    pairs = _baseline_best_match(baseline_lemmas, predicted_definitions)
    return {
        lemma.name: agent_name
        for lemma, (agent_name, _sim) in zip(baseline_lemmas, pairs)
    }


def compute_m2(
    baseline_lemmas: Sequence[BaselineLemmaSpec],
    predicted_definitions: Mapping[str, str],
) -> float:
    """Per-protocol M2: arithmetic mean of per-baseline best similarity.

    For every baseline lemma we take the maximum
    ``lemma_similarity`` over the agent's predicted lemmas (alpha
    equivalence pegs the score at 1.0; unrelated lemmas drift toward
    0). The protocol-level M2 is the mean of those per-baseline
    scores. Baselines whose formula fails to parse, and the case where
    the agent emits no parsable lemma, contribute 0. Empty baseline
    yields 1.0 (vacuous, aligned with M1 / M4).
    """

    n = len(baseline_lemmas)
    if n == 0:
        return 1.0
    pairs = _baseline_best_match(baseline_lemmas, predicted_definitions)
    return sum(sim for _name, sim in pairs) / n


def compute_m3(
    baseline_lemmas: Sequence[BaselineLemmaSpec],
    name_map: Mapping[str, str | None],
    predicted_verdicts: Mapping[str, TamarinVerdict | None],
    predicted_steps: Mapping[str, int | None] | None = None,
) -> float:
    """Per-protocol M3: verdict-consistent lemmas weighted by trace length.

    ``name_map`` is the similarity alignment (``align_by_similarity``)
    shared with M5: each baseline lemma is paired with the predicted
    lemma whose formula similarity is highest. The denominator is the
    number of paired baselines — unpaired ones (predicted list empty /
    unparsable) drop out of the ratio entirely. Empty baseline yields
    1.0 (vacuous); non-empty baseline with zero pairs yields 0.0.

    Per-pair score:
      * verdict mismatch (or missing predicted verdict)             → 0
      * both ``falsified`` (consistent)                              → 1
      * both ``verified`` (consistent), and we have step counts on
        both sides                                                   →
            ``min(predicted_steps / baseline.verified_steps, 1.0)``
        — penalises a predicted proof that is *shorter* than the
        baseline (suspected of skipping work) while capping at 1 so a
        longer proof is not punished. Returns 1.0 when either side's
        step count is missing (graceful fallback to verdict
        consistency).
    """

    if not baseline_lemmas:
        return 1.0
    predicted_steps = predicted_steps or {}
    matched = 0
    score_sum = 0.0
    for lemma in baseline_lemmas:
        agent_name = name_map.get(lemma.name)
        if agent_name is None:
            continue
        matched += 1
        agent_verdict = predicted_verdicts.get(agent_name)
        if agent_verdict != lemma.outcome:
            continue  # verdict mismatch → 0 contribution
        if lemma.outcome != TamarinVerdict.VERIFIED:
            score_sum += 1.0
            continue
        base_steps = lemma.verified_steps
        exp_steps = predicted_steps.get(agent_name)
        if base_steps is None or exp_steps is None or base_steps <= 0:
            score_sum += 1.0  # missing step data → fall back to verdict-only
            continue
        score_sum += min(exp_steps / base_steps, 1.0)
    if matched == 0:
        return 0.0
    return score_sum / matched


def compute_m4(
    predicted: Mapping[str, Sequence[ActionBlock] | None],
    baseline: Mapping[str, Sequence[ActionBlock]],
) -> float:
    """Per-protocol M4: mean of per-variant M1.

    Each baseline variant id is scored with ``compute_m1`` against the
    same-id predicted blocks (treated as empty when missing). The
    overall M4 is the arithmetic mean over baseline variants. Empty
    baseline yields 1.0.
    """

    n = len(baseline)
    if n == 0:
        return 1.0
    total = 0.0
    for variant_id, baseline_blocks in baseline.items():
        predicted_blocks = predicted.get(variant_id) or ()
        total += compute_m1(predicted_blocks, baseline_blocks)
    return total / n


def compute_m5(
    predicted: Mapping[str, Mapping[str, TamarinVerdict | None]],
    baseline_targets: Mapping[str, Sequence[str]],
    name_map: Mapping[str, str | None],
) -> float:
    """Per-protocol M5: weakness-variant hit rate via the shared alignment.

    For each baseline target ``(w, ell)`` the baseline lemma name ``ell``
    is mapped to the agent's name through ``name_map`` before looking up
    the variant's verdict. A hit requires ``TamarinVerdict.FALSIFIED``;
    an unmapped baseline name, missing variant, missing agent lemma, or
    any other verdict is a non-hit. Empty target set yields 1.0.
    """

    total = sum(len(targets) for targets in baseline_targets.values())
    if total == 0:
        return 1.0
    hits = 0
    for variant_id, target_lemmas in baseline_targets.items():
        variant_predicted = predicted.get(variant_id) or {}
        for lemma_name in target_lemmas:
            agent_name = name_map.get(lemma_name)
            if agent_name is None:
                continue
            if variant_predicted.get(agent_name) is TamarinVerdict.FALSIFIED:
                hits += 1
    return hits / total
