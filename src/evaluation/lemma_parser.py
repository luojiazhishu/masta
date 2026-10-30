from __future__ import annotations

import re

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
    VAR_SORT_FRESH,
    VAR_SORT_MESSAGE,
    VAR_SORT_PUBLIC,
    VAR_SORT_TEMPORAL,
    Var,
)


class LemmaParseError(ValueError):
    """Raised when a Tamarin lemma definition is malformed."""


_TOKEN_RE = re.compile(
    r"""
        \s+
      | //[^\n]*
      | "
      | '[^']*'
      | ==>
      | [#~$]?[A-Za-z_][A-Za-z0-9_]*
      | [(),.<>\[\]:@&|=+;]
      | \S
    """,
    re.VERBOSE,
)

_BARE_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_PREFIXED_IDENT_RE = re.compile(r"[#~$][A-Za-z_][A-Za-z0-9_]*\Z")

_KEYWORDS = frozenset({"lemma", "All", "Ex", "not"})


def parse_lemma(text: str) -> Lemma:
    tokens = _tokenize(text)
    parser = _Parser(tokens)
    lemma = parser.parse_lemma()
    parser.expect_end()
    return lemma


def _tokenize(text: str) -> list[str]:
    out: list[str] = []
    for match in _TOKEN_RE.finditer(text):
        token = match.group(0)
        if token.isspace() or token.startswith("//"):
            continue
        out.append(token)
    return out


def _is_bare_identifier(token: str | None) -> bool:
    return token is not None and bool(_BARE_IDENT_RE.match(token))


def _is_prefixed_identifier(token: str | None) -> bool:
    return token is not None and bool(_PREFIXED_IDENT_RE.match(token))


def _is_var_token(token: str | None) -> bool:
    if token is None:
        return False
    if token in _KEYWORDS:
        return False
    return _is_bare_identifier(token) or _is_prefixed_identifier(token)


def _split_var_prefix(token: str) -> tuple[str, str]:
    if token and token[0] in {"#", "~", "$"}:
        return token[0], token[1:]
    return "", token


def _sort_for_prefix(prefix: str) -> str:
    if prefix == "#":
        return VAR_SORT_TEMPORAL
    if prefix == "~":
        return VAR_SORT_FRESH
    if prefix == "$":
        return VAR_SORT_PUBLIC
    return VAR_SORT_MESSAGE


class _Parser:
    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens
        self._pos = 0
        self._env: list[dict[str, str]] = []

    def parse_lemma(self) -> Lemma:
        self._consume("lemma")
        if not _is_bare_identifier(self._peek()):
            raise LemmaParseError(
                f"expected lemma name, got {self._peek()!r}"
            )
        name = self._advance()
        options: tuple[str, ...] = ()
        if self._peek() == "[":
            options = self._parse_options()
        self._consume(":")
        self._consume('"')
        formula = self._parse_formula()
        self._consume('"')
        return Lemma(name=name, options=options, formula=formula)

    def expect_end(self) -> None:
        if self._pos != len(self._tokens):
            raise LemmaParseError(
                f"unexpected trailing token {self._peek()!r}"
            )

    def _parse_options(self) -> tuple[str, ...]:
        self._consume("[")
        if not _is_bare_identifier(self._peek()):
            raise LemmaParseError(
                f"expected option identifier, got {self._peek()!r}"
            )
        opts = [self._advance()]
        while self._peek() == ",":
            self._consume(",")
            if not _is_bare_identifier(self._peek()):
                raise LemmaParseError(
                    f"expected option identifier after ',', got {self._peek()!r}"
                )
            opts.append(self._advance())
        self._consume("]")
        return tuple(opts)

    def _parse_formula(self) -> Formula:
        return self._parse_implication()

    def _parse_implication(self) -> Formula:
        left = self._parse_quantified_or_disjunction()
        while self._peek() == "==>":
            self._consume("==>")
            right = self._parse_quantified_or_disjunction()
            left = Implies(left=left, right=right)
        return left

    def _parse_quantified_or_disjunction(self) -> Formula:
        if self._peek() in ("All", "Ex"):
            return self._parse_quantifier()
        return self._parse_disjunction()

    def _parse_quantifier(self) -> Formula:
        kind = self._advance()
        bound: list[Var] = []
        frame: dict[str, str] = {}
        if self._peek() == ".":
            raise LemmaParseError("quantifier must bind at least one variable")
        while self._peek() != "." and self._peek() is not None:
            tok = self._advance()
            if not _is_var_token(tok):
                raise LemmaParseError(
                    f"expected bound variable, got {tok!r}"
                )
            prefix, name = _split_var_prefix(tok)
            sort = _sort_for_prefix(prefix)
            frame[name] = sort
            bound.append(Var(name=name, sort=sort))
        self._consume(".")
        self._env.append(frame)
        try:
            body = self._parse_formula()
        finally:
            self._env.pop()
        cls = Forall if kind == "All" else Exists
        return cls(vars=tuple(bound), body=body)

    def _parse_disjunction(self) -> Formula:
        left = self._parse_conjunction()
        while self._peek() == "|":
            self._consume("|")
            right = self._parse_conjunction()
            left = Or(left=left, right=right)
        return left

    def _parse_conjunction(self) -> Formula:
        left = self._parse_unary()
        while self._peek() == "&":
            self._consume("&")
            right = self._parse_unary()
            left = And(left=left, right=right)
        return left

    def _parse_unary(self) -> Formula:
        if self._peek() == "not":
            self._consume("not")
            return Not(formula=self._parse_unary())
        return self._parse_atom()

    def _parse_atom(self) -> Formula:
        tok = self._peek()
        if tok == "(":
            self._consume("(")
            inner = self._parse_formula()
            self._consume(")")
            return inner
        if tok in ("All", "Ex"):
            return self._parse_quantifier()
        return self._parse_predicate()

    def _parse_predicate(self) -> Formula:
        if _is_bare_identifier(self._peek()) and self._peek_at(1) == "(":
            name = self._advance()
            self._consume("(")
            args = self._parse_arg_list()
            self._consume(")")
            self._consume("@")
            timepoint = self._parse_var_reference()
            return Action(name=name, args=args, timepoint=timepoint)
        left = self._parse_var_reference()
        op = self._advance()
        if op == "=":
            right = self._parse_var_reference()
            return Equation(left=left, right=right)
        if op == "<":
            right = self._parse_var_reference()
            return Order(left=left, right=right)
        raise LemmaParseError(
            f"expected '=' or '<' between variables, got {op!r}"
        )

    def _parse_arg_list(self) -> tuple[Term, ...]:
        if self._peek() == ")":
            return ()
        args = [self._parse_term()]
        while self._peek() == ",":
            self._consume(",")
            args.append(self._parse_term())
        return tuple(args)

    def _parse_term(self) -> Term:
        left = self._parse_atom_term()
        if self._peek() != "+":
            return left
        elements: list[Term] = [left]
        while self._peek() == "+":
            self._consume("+")
            elements.append(self._parse_atom_term())
        return Sum(elements=tuple(elements))

    def _parse_atom_term(self) -> Term:
        tok = self._peek()
        if tok == "<":
            self._consume("<")
            elements = [self._parse_term()]
            while self._peek() == ",":
                self._consume(",")
                elements.append(self._parse_term())
            self._consume(">")
            return Tuple(elements=tuple(elements))
        if tok is not None and tok.startswith("'") and tok.endswith("'"):
            self._advance()
            return Const(value=tok)
        if _is_var_token(tok):
            ident = self._advance()
            if self._peek() == "(":
                if _is_prefixed_identifier(ident):
                    raise LemmaParseError(
                        f"function symbol must not carry sort prefix: {ident!r}"
                    )
                self._consume("(")
                args = self._parse_arg_list()
                self._consume(")")
                return App(name=ident, args=args)
            return self._materialize_var(ident)
        raise LemmaParseError(
            f"unexpected term token {tok!r}"
        )

    def _parse_var_reference(self) -> Var:
        tok = self._advance()
        if not _is_var_token(tok):
            raise LemmaParseError(
                f"expected variable reference, got {tok!r}"
            )
        return self._materialize_var(tok)

    def _materialize_var(self, token: str) -> Var:
        prefix, name = _split_var_prefix(token)
        for frame in reversed(self._env):
            if name in frame:
                return Var(name=name, sort=frame[name])
        return Var(name=name, sort=_sort_for_prefix(prefix))

    def _peek(self) -> str | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _peek_at(self, offset: int) -> str | None:
        idx = self._pos + offset
        return self._tokens[idx] if idx < len(self._tokens) else None

    def _advance(self) -> str:
        if self._pos >= len(self._tokens):
            raise LemmaParseError("unexpected end of input")
        token = self._tokens[self._pos]
        self._pos += 1
        return token

    def _consume(self, expected: str) -> str:
        token = self._advance()
        if token != expected:
            raise LemmaParseError(
                f"expected {expected!r}, got {token!r}"
            )
        return token
