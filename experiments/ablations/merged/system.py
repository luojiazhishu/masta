"""Merged ablation: Arbiter + single MergedExpertAgent.

Subclasses MastaSystem and replaces both ``self.architect`` and
``self.auditor`` with the same MergedExpertAgent instance. The Arbiter
loop runs unchanged; every ``schedule_initial`` / ``schedule_revision``
regardless of ``target_role`` dispatches to the merged expert because
both attributes point to the same object.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from src.agents.handlers import make_files_handler, make_tamarin_handler
from src.orchestration import FILES_READ_EXTRA_DIRS, MastaSystem

from experiments.ablations.merged.merged_expert import (
    MERGED_WRITE_WHITELIST,
    MergedExpertAgent,
)


class MergedSystem(MastaSystem):
    """Arbiter + one merged Architect/Auditor expert."""

    def __init__(
        self,
        *,
        work_dir: Path,
        runner: Any,
        llm_caller: Callable[[str, str], str],
    ) -> None:
        super().__init__(work_dir=work_dir, runner=runner, llm_caller=llm_caller)
        trace_dir = self.work_dir / "trace"
        merged = MergedExpertAgent(
            files_handler=make_files_handler(
                work_dir=self.work_dir,
                write_whitelist=MERGED_WRITE_WHITELIST,
                role="merged",
                read_extra_dirs=FILES_READ_EXTRA_DIRS,
            ),
            tamarin_handler=make_tamarin_handler(runner, work_dir=self.work_dir),
            llm_caller=lambda p: llm_caller("merged", p),
            trace_dir=trace_dir,
        )
        self.architect = merged
        self.auditor = merged
