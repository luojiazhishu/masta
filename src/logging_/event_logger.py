from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

import structlog

from src.model.tamarin_result import TamarinResult


LOGGER_NAME = "masta.events"
EVENTS_FILENAME = "events.jsonl"


def configure_logging(output_dir: Path, run_id: str | None = None) -> str:
    """Set up structured JSON-line logging into ``output_dir/events.jsonl``.

    Returns the run id (auto-generated when not supplied) so callers can
    correlate prints and structured records. Idempotent: rebinds the file
    handler on every call so successive runs do not append to a stale
    handle while still preserving the file's existing content.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / EVENTS_FILENAME

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    for existing in list(logger.handlers):
        existing.close()
        logger.removeHandler(existing)
    logger.addHandler(handler)
    logger.propagate = False

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )

    rid = run_id or uuid.uuid4().hex[:12]
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(run_id=rid)
    return rid


class EventLogger:
    """Thin wrapper around the configured structlog logger.

    Each ``log_*`` helper writes one JSON line into ``events.jsonl``. Use
    bare ``log_event(stage, event_type, **kwargs)`` for ad-hoc records;
    use the typed helpers (``log_llm_call`` / ``log_tamarin_call``) for
    the two boundaries we care most about cost-wise.
    """

    def __init__(self) -> None:
        self._log = structlog.get_logger(LOGGER_NAME)

    def log_event(self, stage: str, event_type: str, **kwargs: Any) -> None:
        self._log.info(event_type, stage=stage, **kwargs)

    def log_llm_call(
        self,
        *,
        agent_role: str,
        prompt_tokens: int,
        completion_tokens: int,
        wall_time_seconds: float,
    ) -> None:
        self._log.info(
            "llm_call",
            agent_role=agent_role,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            wall_time_seconds=wall_time_seconds,
        )

    def log_tamarin_call(self, result: TamarinResult) -> None:
        self._log.info(
            "tamarin_call",
            verdict=result.verdict.value,
            lemma_name=result.lemma_name,
            wall_time_seconds=result.wall_time_seconds,
        )
