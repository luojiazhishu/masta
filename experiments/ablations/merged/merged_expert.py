"""MergedExpertAgent: Architect ∪ Auditor in a single agent.

The write whitelist is the union of both whitelists. The role and
responsibilities descriptions concatenate the two prompts, with a
brief header clarifying that this agent holds both duties simultaneously.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from src.agents.architect import ARCHITECT_WRITE_WHITELIST
from src.agents.auditor import AUDITOR_WRITE_WHITELIST
from src.agents.base import BaseAgent
from src.agents.prompts import architect as arch_prompts
from src.agents.prompts import auditor as aud_prompts

MERGED_WRITE_WHITELIST: frozenset[str] = (
    ARCHITECT_WRITE_WHITELIST | AUDITOR_WRITE_WHITELIST
)

_MERGED_ROLE_DESP: str = (
    "You are the Expert, a Tamarin modeling and security specialist who "
    "simultaneously holds two roles:\n\n"
    "[Architect role] " + arch_prompts.ROLE_DESP + "\n\n"
    "[Auditor role] " + aud_prompts.ROLE_DESP
)

_MERGED_RESPONSIBILITIES_DESP: str = (
    "## Architect responsibilities\n\n"
    + arch_prompts.RESPONSIBILITIES_DESP.strip()
    + "\n\n## Auditor responsibilities\n\n"
    + aud_prompts.RESPONSIBILITIES_DESP.strip()
)


class MergedExpertAgent(BaseAgent):
    """Single agent combining Architect and Auditor duties."""

    def __init__(
        self,
        *,
        files_handler: Callable[[dict[str, Any]], Any],
        tamarin_handler: Callable[[dict[str, Any]], Any],
        llm_caller: Callable[[str], str],
        trace_dir: Path | None = None,
    ) -> None:
        super().__init__(
            name="merged",
            role_desp=_MERGED_ROLE_DESP,
            responsibilities_desp=_MERGED_RESPONSIBILITIES_DESP,
            trace_dir=trace_dir,
        )
        self.register_capability("files", files_handler)
        self.register_capability("tamarin", tamarin_handler)
        self.register_capability("end", lambda args: None)
        self.attach_llm(llm_caller)
