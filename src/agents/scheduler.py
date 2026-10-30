from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.agents.problem_type import (
    DirectionTextError,
    ProblemType,
    default_target,
    format_direction_text,
    parse_direction_text,
    validate_target_assignment,
)


@dataclass(frozen=True)
class ModificationEntry:
    target_role: str
    target_artifact: str
    direction_text: str
    problem_type: ProblemType
    issued_by: str
    created_at: str
    initial: bool = False

    def to_dict(self) -> dict[str, Any]:
        # ``created_at`` is intentionally omitted from the agent-facing
        # observation: it is a wall-clock ISO timestamp that would
        # break the LLM-call cache for every Arbiter turn that follows
        # a schedule_*. The field stays on the dataclass instance for
        # in-process bookkeeping.
        return {
            "target_role": self.target_role,
            "target_artifact": self.target_artifact,
            "direction_text": self.direction_text,
            "problem_type": self.problem_type.value,
            "issued_by": self.issued_by,
            "initial": self.initial,
        }


@dataclass
class Scheduler:
    """In-process scheduler for the MASTA workflow.

    The Arbiter is the only role allowed to call this. ``schedule_initial``
    drives the default sequence; ``schedule_revision`` carries a six-class
    problem-type label and is cross-validated against the default
    ``(target_role, target_artifact)`` map.
    """

    issuer: str = "arbiter"
    history: list[ModificationEntry] = field(default_factory=list)
    duplicate_window: int = 2

    def schedule_initial(
        self,
        target_role: str,
        task_label: str,
        brief: str,
    ) -> ModificationEntry:
        entry = ModificationEntry(
            target_role=_normalize_role(target_role),
            target_artifact=_initial_artifact(task_label),
            direction_text=brief,
            problem_type=ProblemType.OTHER,
            issued_by=self.issuer,
            created_at=datetime.now(UTC).isoformat(),
            initial=True,
        )
        if self._is_recent_duplicate(entry):
            raise DirectionTextError(
                "duplicate of a recent directive; revise the brief"
            )
        self.history.append(entry)
        return entry

    def schedule_revision(
        self,
        target_role: str,
        target_artifact: str,
        problem_type: str | ProblemType,
        brief: str,
    ) -> ModificationEntry:
        target_role = _normalize_role(target_role)
        if isinstance(problem_type, ProblemType):
            pt = problem_type
        else:
            try:
                pt = ProblemType(problem_type)
            except ValueError as exc:
                raise DirectionTextError(
                    f"unknown problem-type label: {problem_type!r}"
                ) from exc
        if pt is ProblemType.OTHER:
            if not (target_role and target_artifact):
                raise DirectionTextError(
                    "OTHER directives require explicit target_role and target_artifact"
                )
        else:
            expected = default_target(pt)
            if expected and (target_role, target_artifact) != expected:
                raise DirectionTextError(
                    f"problem_type {pt.value!r} expects target {expected!r}, "
                    f"got ({target_role!r}, {target_artifact!r})"
                )
        body = brief.strip()
        direction_text = format_direction_text(pt, body)
        # Sanity-check by re-parsing.
        parse_direction_text(direction_text)
        validate_target_assignment(pt, target_role, target_artifact)
        entry = ModificationEntry(
            target_role=target_role,
            target_artifact=target_artifact,
            direction_text=direction_text,
            problem_type=pt,
            issued_by=self.issuer,
            created_at=datetime.now(UTC).isoformat(),
            initial=False,
        )
        if self._is_recent_duplicate(entry):
            raise DirectionTextError(
                "identical directive issued in the previous round; revise the brief"
            )
        self.history.append(entry)
        return entry

    def _is_recent_duplicate(self, entry: ModificationEntry) -> bool:
        recent = self.history[-self.duplicate_window :]
        return any(
            e.target_role == entry.target_role
            and e.target_artifact == entry.target_artifact
            and e.problem_type is entry.problem_type
            and e.direction_text == entry.direction_text
            for e in recent
        )


_INITIAL_ARTIFACT_BY_LABEL: dict[str, str] = {
    "serialize_actions": "action_blocks_draft.yaml",
    "extract_security_requirements": "threat_model.md",
    "model_rules": "model.spthy",
    "model_properties": "model.spthy",
    "finalize": "model.spthy",
}


def _initial_artifact(task_label: str) -> str:
    artifact = _INITIAL_ARTIFACT_BY_LABEL.get(task_label)
    if artifact is None:
        raise DirectionTextError(f"unknown task_label {task_label!r}")
    return artifact


def _normalize_role(value: str) -> str:
    """Lowercase + strip the LLM-supplied target_role so downstream
    routing keys (``architect`` / ``auditor``) match regardless of the
    case the model happens to emit (``Architect`` / ``AUDITOR`` / etc.)."""
    if not isinstance(value, str):
        return ""
    return value.strip().lower()
