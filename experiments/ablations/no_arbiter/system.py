"""No-Arbiter ablation: Architect + Auditor without an Arbiter.

Runs DEFAULT_SEQUENCE in fixed order. Each stage is dispatched to its
canonical role:

  serialize_actions             → architect  (action_blocks_draft.yaml)
  extract_security_requirements → auditor    (threat_model.md)
  model_rules                   → architect  (model.spthy)
  model_properties              → auditor    (model.spthy)
  finalize                      → architect  (model.spthy)

Each expert drives its own ReAct loop until ended or until a concern
surfaces. Concerns are treated as ended — no arbiter is present to
issue revisions. Execution continues to the next stage regardless.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from src.agents.architect import ARCHITECT_WRITE_WHITELIST, ArchitectAgent
from src.agents.auditor import AUDITOR_WRITE_WHITELIST, AuditorAgent
from src.agents.handlers import make_files_handler, make_tamarin_handler
from src.console import log, masta_prefix

TAMARIN_MANUAL_DIR = Path(__file__).resolve().parents[4] / "data" / "tamarin"
FILES_READ_EXTRA_DIRS = {"tamarin_manual": TAMARIN_MANUAL_DIR}

_STAGE_PLAN: tuple[tuple[str, str, str], ...] = (
    ("serialize_actions",             "architect", "action_blocks_draft.yaml"),
    ("extract_security_requirements", "auditor",   "threat_model.md"),
    ("model_rules",                   "architect", "model.spthy"),
    ("model_properties",              "auditor",   "model.spthy"),
    ("finalize",                      "architect", "model.spthy"),
)


class NoArbiterSystem:
    """Architect + Auditor pipeline with no Arbiter orchestration."""

    def __init__(
        self,
        *,
        work_dir: Path,
        runner: Any,
        llm_caller: Callable[[str, str], str],
    ) -> None:
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
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

    def run(self, *, initial_document: str) -> str:
        (self.work_dir / "document.txt").write_text(
            initial_document, encoding="utf-8"
        )
        log(f"{masta_prefix()} [no-arbiter] starting fixed-sequence run")

        for stage, role, artifact in _STAGE_PLAN:
            agent = self.architect if role == "architect" else self.auditor
            brief = (
                f"task_label: {stage}\n"
                f"brief: complete this stage of the modeling pipeline\n"
                f"target_artifact: {artifact}"
            )
            agent.start_task(brief)
            log(f"{masta_prefix()} [no-arbiter] stage={stage} role={role}")

            while not agent._ended:
                summary = agent.step()
                if summary.get("ended") or summary.get("concern"):
                    if summary.get("concern"):
                        log(
                            f"{masta_prefix()} [no-arbiter] stage={stage} "
                            f"concern (no revision, continuing)"
                        )
                    break

        model_path = self.work_dir / "model.spthy"
        end_reason = "done" if model_path.is_file() else "no_model"
        log(f"{masta_prefix()} [no-arbiter] finished reason={end_reason!r}")
        return end_reason
