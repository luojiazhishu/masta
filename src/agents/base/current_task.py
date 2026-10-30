from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CurrentTask:
    """Structured payload for the prompt's `## CURRENT TASK` section."""

    task_label: str
    brief: str = ""
    target_artifact: str = ""
    problem_type: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        lines = [f"task_label: {self.task_label}"]
        if self.target_artifact:
            lines.append(f"target_artifact: {self.target_artifact}")
        if self.problem_type:
            lines.append(f"problem_type: {self.problem_type}")
        if self.brief:
            lines.append(f"brief: {self.brief}")
        for key, value in self.extras.items():
            lines.append(f"{key}: {value}")
        return "\n".join(lines)
