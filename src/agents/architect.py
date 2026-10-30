from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from src.agents.base import BaseAgent
from src.agents.prompts import architect as prompts


ARCHITECT_WRITE_WHITELIST: frozenset[str] = frozenset(
    {"action_blocks_draft.yaml", "model.spthy"}
)


class ArchitectAgent(BaseAgent):
    """Tamarin modeling expert with action-block + rule duties."""

    def __init__(
        self,
        *,
        files_handler: Callable[[dict[str, Any]], Any],
        tamarin_handler: Callable[[dict[str, Any]], Any],
        llm_caller: Callable[[str], str],
        trace_dir: Path | None = None,
    ) -> None:
        super().__init__(
            name="architect",
            role_desp=prompts.ROLE_DESP,
            responsibilities_desp=prompts.RESPONSIBILITIES_DESP,
            trace_dir=trace_dir,
        )
        self.register_capability("files", files_handler)
        self.register_capability("tamarin", tamarin_handler)
        self.register_capability("end", lambda args: None)
        self.attach_llm(llm_caller)
