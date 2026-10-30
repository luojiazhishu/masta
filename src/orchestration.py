"""MASTA system orchestration — single-file entrypoint.

One ``MastaSystem(work_dir, runner, llm_caller).run(initial_document=...)``
call drives the Arbiter through one long ReAct session. Each
``schedule_initial`` / ``schedule_revision`` activates exactly one expert
synchronously; ``advance`` steps it; the resulting summary is fed back
to the Arbiter as the observation for that action. There is at most one
active expert at any moment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.agents.arbiter import ArbiterAgent
from src.agents.architect import ARCHITECT_WRITE_WHITELIST, ArchitectAgent
from src.agents.auditor import AUDITOR_WRITE_WHITELIST, AuditorAgent
from src.agents.base import BaseAgent
from src.agents.handlers import make_files_handler, make_tamarin_handler
from src.agents.problem_type import (
    DirectionTextError,
    ProblemType,
    parse_direction_text,
)
from src.agents.scheduler import ModificationEntry, Scheduler
from src.console import GREEN, YELLOW, log, masta_prefix, paint


TAMARIN_MANUAL_DIR = Path(__file__).resolve().parents[1] / "data" / "tamarin"
FILES_READ_EXTRA_DIRS: dict[str, Path] = {"tamarin_manual": TAMARIN_MANUAL_DIR}

DEFAULT_SEQUENCE: tuple[str, ...] = (
    "serialize_actions",
    "extract_security_requirements",
    "model_rules",
    "model_properties",
    "finalize",
)

_ARTIFACT_LABELS: dict[str, str] = {
    "action_blocks_draft.yaml": "serialize_actions",
    "threat_model.md": "extract_security_requirements",
    "model.spthy": "model_rules_or_properties",
}


@dataclass
class WorkflowState:
    completed: list[str] = field(default_factory=list)
    current_stage: str | None = None
    current_role: str | None = None
    last_revision: tuple[str, str, str] | None = None


@dataclass
class ActiveExpert:
    agent: BaseAgent
    target_role: str
    target_artifact: str


class MastaSystem:
    def __init__(
        self,
        *,
        work_dir: Path,
        runner: Any,
        llm_caller: Callable[[str, str], str],
    ) -> None:
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.runner = runner
        self.llm_caller = llm_caller

        self.scheduler = Scheduler()
        self.workflow = WorkflowState()
        self.active: ActiveExpert | None = None
        self.dispatched: list[ModificationEntry] = []

        trace_dir = self.work_dir / "trace"
        self.architect = ArchitectAgent(
            files_handler=make_files_handler(
                work_dir=self.work_dir,
                write_whitelist=ARCHITECT_WRITE_WHITELIST,
                role="architect",
                read_extra_dirs=FILES_READ_EXTRA_DIRS,
            ),
            tamarin_handler=make_tamarin_handler(runner, work_dir=self.work_dir),
            llm_caller=lambda p: llm_caller("architect", p),
            trace_dir=trace_dir,
        )
        self.auditor = AuditorAgent(
            files_handler=make_files_handler(
                work_dir=self.work_dir,
                write_whitelist=AUDITOR_WRITE_WHITELIST,
                role="auditor",
                read_extra_dirs=FILES_READ_EXTRA_DIRS,
            ),
            tamarin_handler=make_tamarin_handler(runner, work_dir=self.work_dir),
            llm_caller=lambda p: llm_caller("auditor", p),
            trace_dir=trace_dir,
        )
        self.arbiter = ArbiterAgent(
            files_handler=make_files_handler(
                work_dir=self.work_dir,
                write_whitelist=set(),
                role="arbiter",
                read_extra_dirs=FILES_READ_EXTRA_DIRS,
            ),
            tamarin_handler=make_tamarin_handler(runner, work_dir=self.work_dir),
            scheduler_handler=self._scheduler_handler,
            llm_caller=lambda p: llm_caller("arbiter", p),
            trace_dir=trace_dir,
        )
        self.arbiter.current_task = self._render_task()

    def run(self, *, initial_document: str) -> str:
        (self.work_dir / "document.txt").write_text(
            initial_document, encoding="utf-8"
        )
        self.dispatched = []
        self.arbiter._turn = 0
        self.arbiter._ended = False
        self.arbiter._end_reason = ""
        log(f"{masta_prefix()} starting Arbiter ReAct session")
        last_reason = ""
        while not self.arbiter._ended:
            summary = self.arbiter.step()
            if summary["ended"]:
                last_reason = summary["end_reason"]
        log(
            f"{masta_prefix()} arbiter ended "
            f"reason={paint(repr(last_reason), GREEN)}"
        )
        return last_reason

    def _scheduler_handler(self, args: dict[str, Any]) -> Any:
        action = args.get("action")
        if action == "advance":
            return self._advance_active()
        if action == "schedule_initial":
            return self._schedule(args, initial=True)
        if action == "schedule_revision":
            return self._schedule(args, initial=False)
        return {"error": f"unknown scheduler action {action!r}"}

    def _schedule(
        self, args: dict[str, Any], *, initial: bool
    ) -> dict[str, Any]:
        try:
            if initial:
                entry = self.scheduler.schedule_initial(
                    target_role=str(args.get("target_role", "")),
                    task_label=str(args.get("task_label", "")),
                    brief=str(args.get("brief", "")),
                )
            else:
                entry = self.scheduler.schedule_revision(
                    target_role=str(args.get("target_role", "")),
                    target_artifact=str(args.get("target_artifact", "")),
                    problem_type=str(args.get("problem_type", "")),
                    brief=str(args.get("brief", "")),
                )
        except DirectionTextError as exc:
            return {"error": str(exc)}
        self.dispatched.append(entry)
        summary = self._activate(entry)
        return {
            **entry.to_dict(),
            "started": True,
            "summary": summary,
        }

    def _advance_active(self) -> dict[str, Any]:
        if self.active is None:
            return {"error": "no active expert"}
        summary = self._run_until_escalation(self.active.agent)
        if summary["ended"]:
            self._mark_stage_done()
            self.active = None
        return {"summary": summary}

    @staticmethod
    def _run_until_escalation(agent: BaseAgent) -> dict[str, Any]:
        """Drive an active expert through its own ReAct loop until it
        either raises a non-empty ``concern`` or signals ``ended=true``.
        Each internal step contributes one compact line to ``turn_log``
        so the Arbiter can see what the expert did since the last
        escalation; the log is appended to the returned summary and
        thereby reaches the Arbiter as part of the observation.
        """
        log_lines: list[str] = []
        while True:
            summary = agent.step()
            line = _format_turn_line(agent._turn, summary)
            if line:
                log_lines.append(line)
            if summary.get("ended") or summary.get("concern"):
                # Tail-clip so a long expert run does not blow up the
                # Arbiter's prompt. 30 lines covers the loop / stalled
                # patterns the Arbiter is asked to detect.
                summary["turn_log"] = "\n".join(log_lines[-30:])
                return summary

    def _activate(self, entry: ModificationEntry) -> dict[str, Any]:
        if self.active is not None:
            self.active.agent.cancel("superseded by new schedule")
            self.active = None
        target_agent = (
            self.architect if entry.target_role == "architect" else self.auditor
        )
        problem_type = (
            entry.problem_type.value
            if isinstance(entry.problem_type, ProblemType)
            else str(entry.problem_type)
        )
        stage = self._stage_for(entry.target_role, entry.target_artifact)
        if entry.initial:
            log(
                f"{masta_prefix()} dispatch_initial -> {entry.target_role} "
                f"stage={stage} artifact={entry.target_artifact}"
            )
        else:
            log(
                f"{masta_prefix()} dispatch_revision -> {entry.target_role} "
                f"artifact={entry.target_artifact} "
                f"problem_type={paint(problem_type, YELLOW)}"
            )
        target_agent.start_task(self._format_brief(entry))
        self.workflow.current_stage = stage
        self.workflow.current_role = entry.target_role
        self.workflow.last_revision = (
            None
            if entry.initial
            else (problem_type, entry.target_role, entry.target_artifact)
        )
        self.arbiter.current_task = self._render_task()
        summary = self._run_until_escalation(target_agent)
        if summary["ended"]:
            self._mark_stage_done()
        else:
            self.active = ActiveExpert(
                agent=target_agent,
                target_role=entry.target_role,
                target_artifact=entry.target_artifact,
            )
        return summary

    def _mark_stage_done(self) -> None:
        if self.workflow.last_revision is not None:
            self.workflow.last_revision = None
        elif self.workflow.current_stage is not None:
            self.workflow.completed.append(self.workflow.current_stage)
        self.workflow.current_stage = None
        self.workflow.current_role = None
        self.arbiter.current_task = self._render_task()

    @staticmethod
    def _stage_for(role: str, artifact: str) -> str:
        if artifact == "action_blocks_draft.yaml":
            return "serialize_actions"
        if artifact == "threat_model.md":
            return "extract_security_requirements"
        if artifact == "model.spthy":
            return "model_properties" if role == "auditor" else "model_rules"
        return "execute"

    @staticmethod
    def _format_brief(entry: ModificationEntry) -> str:
        if entry.initial:
            label = _ARTIFACT_LABELS.get(entry.target_artifact, "execute")
            return (
                f"task_label: {label}\n"
                f"brief: {entry.direction_text}\n"
                f"target_artifact: {entry.target_artifact}"
            )
        parsed = parse_direction_text(entry.direction_text)
        return (
            f"task_label: revise\n"
            f"target_artifact: {entry.target_artifact}\n"
            f"problem_type: {parsed.problem_type.value}\n"
            f"brief: {parsed.body}"
        )

    def _render_task(self) -> str:
        s = self.workflow
        completed = ", ".join(s.completed) if s.completed else "(none)"
        if s.current_stage:
            current = f"{s.current_stage} ({s.current_role}, running)"
        else:
            current = "(no expert active)"
        if s.completed and s.completed[-1] in DEFAULT_SEQUENCE:
            idx = DEFAULT_SEQUENCE.index(s.completed[-1]) + 1
            nxt = (
                DEFAULT_SEQUENCE[idx]
                if idx < len(DEFAULT_SEQUENCE)
                else DEFAULT_SEQUENCE[-1]
            )
        else:
            nxt = DEFAULT_SEQUENCE[0]
        if s.last_revision:
            pt, tr, ta = s.last_revision
            rev = f"{pt} -> {tr}/{ta} (in flight)"
        else:
            rev = "(none)"
        return (
            "task_label: kickoff\n"
            "brief: drive the default sequence (serialize_actions -> "
            "extract_security_requirements -> model_rules -> "
            "model_properties -> finalize) by issuing one schedule_initial "
            "per stage, advancing the active expert until summary.ended is "
            "true, then advancing to the next stage. Use schedule_revision "
            "when a summary.concern matches one of the six problem types.\n"
            "\n"
            "WORKFLOW STATE (authoritative — overrides any conflicting "
            "CONTEXT):\n"
            f"- completed stages: {completed}\n"
            f"- current stage:    {current}\n"
            f"- next default stage if current ends: {nxt}\n"
            f"- last revision:    {rev}"
        )


def _format_turn_line(turn_idx: int, summary: dict[str, Any]) -> str:
    parts: list[str] = [f"[t{turn_idx}]"]
    for key, label, limit in (
        ("did", "did", 100),
        ("last_verification", "verif", 100),
        ("outstanding", "out", 80),
        ("concern", "concern", 100),
    ):
        value = (summary.get(key) or "").strip()
        if value:
            if len(value) > limit:
                value = value[: limit - 1] + "…"
            parts.append(f"{label}={value}")
    if summary.get("ended"):
        reason = (summary.get("end_reason") or "").strip()
        if len(reason) > 80:
            reason = reason[:79] + "…"
        parts.append(f"ended reason={reason!r}")
    if len(parts) == 1:
        return ""
    return " | ".join(parts)
