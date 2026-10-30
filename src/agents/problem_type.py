from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ProblemType(str, Enum):
    RULE_UNREACHABLE = "rule unreachable"
    PROPERTY_DISAGREES_WITH_PROTOCOL = "property disagrees with protocol"
    THREAT_MODEL_OMISSION = "threat-model omission"
    LEMMA_SYNTAX_ERROR = "lemma syntax error"
    LEMMA_REVISION_REQUIRED = "lemma revision required"
    EXECUTABLE_VERIFICATION_FAILURE = "executable-verification failure"
    OTHER = "other"


_DEFAULT_TARGET: dict[ProblemType, tuple[str, str]] = {
    ProblemType.RULE_UNREACHABLE: ("architect", "model.spthy"),
    ProblemType.EXECUTABLE_VERIFICATION_FAILURE: ("architect", "model.spthy"),
    ProblemType.PROPERTY_DISAGREES_WITH_PROTOCOL: ("auditor", "model.spthy"),
    ProblemType.LEMMA_SYNTAX_ERROR: ("auditor", "model.spthy"),
    ProblemType.LEMMA_REVISION_REQUIRED: ("auditor", "model.spthy"),
    ProblemType.THREAT_MODEL_OMISSION: ("auditor", "threat_model.md"),
}

OTHER_SUBTYPE_PREFIX = "subtype:"
DIRECTION_TEXT_SEPARATOR = "\n"


def default_target(problem_type: ProblemType) -> tuple[str, str] | None:
    return _DEFAULT_TARGET.get(problem_type)


@dataclass(frozen=True)
class ParsedDirection:
    problem_type: ProblemType
    body: str


class DirectionTextError(ValueError):
    """Raised when direction_text cannot be split into (problem_type, body)."""


def parse_direction_text(direction_text: str) -> ParsedDirection:
    raw = direction_text.lstrip()
    if not raw:
        raise DirectionTextError("direction_text is empty")
    head, sep, tail = raw.partition(DIRECTION_TEXT_SEPARATOR)
    label = head.strip()
    body = tail.lstrip() if sep else ""
    try:
        problem_type = ProblemType(label)
    except ValueError as exc:
        raise DirectionTextError(
            f"unknown problem-type label: {label!r}"
        ) from exc
    if problem_type is ProblemType.OTHER:
        if not body.startswith(OTHER_SUBTYPE_PREFIX):
            raise DirectionTextError(
                "OTHER direction_text must start its body with 'subtype:'"
            )
    return ParsedDirection(problem_type=problem_type, body=body)


def validate_target_assignment(
    problem_type: ProblemType,
    target_role: str,
    target_artifact: str,
) -> None:
    expected = default_target(problem_type)
    if expected is None:
        return
    if (target_role, target_artifact) != expected:
        raise DirectionTextError(
            f"problem_type {problem_type.value!r} expects target "
            f"{expected!r}, got ({target_role!r}, {target_artifact!r})"
        )


def format_direction_text(problem_type: ProblemType, body: str) -> str:
    if problem_type is ProblemType.OTHER and not body.startswith(
        OTHER_SUBTYPE_PREFIX
    ):
        raise DirectionTextError(
            "OTHER direction_text body must start with 'subtype:'"
        )
    return f"{problem_type.value}{DIRECTION_TEXT_SEPARATOR}{body}".rstrip()
