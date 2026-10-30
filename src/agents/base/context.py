from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

ENTRY_LIMIT = 20
CHAR_LIMIT = 100_000
COMPACT_WINDOW = 8
RESULT_RENDER_LIMIT = 30_000
COMPACT_FALLBACK_PREFIX = "[compact-fallback]"


def _is_files_read(operation: str) -> bool:
    """Return True when the recorded operation is a ``files.read``
    invocation.

    The agent loop encodes a turn as the JSON of the ReAct step
    ``{"thought": ..., "action": {"capability": ..., "args": ...}}``.
    ``files.read`` results carry the full text body of an artefact
    (document.txt, model.spthy, threat_model.md, ...) which the agent's
    next turn needs to see in full — they are exempt from the
    ``RESULT_RENDER_LIMIT`` truncation that protects against verbose
    Tamarin / scheduler outputs.
    """
    if not operation:
        return False
    try:
        payload = json.loads(operation)
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    action = payload.get("action")
    if not isinstance(action, dict):
        return False
    if action.get("capability") != "files":
        return False
    args = action.get("args")
    return isinstance(args, dict) and args.get("action") == "read"


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"<truncated {len(text) - limit} chars>"


@dataclass
class _Slot:
    operation: str
    result: Any
    size_chars: int = 0


@dataclass
class Context:
    _entries: list[_Slot] = field(default_factory=list)
    _total_chars: int = 0
    _verbose: dict[int, str] = field(default_factory=dict)
    summarizer: Callable[[list[tuple[str, Any]]], str] | None = None

    def add(self, operation: str, result: Any) -> None:
        if self._is_context_full():
            self.compact_context(self.summarizer)
        op_text = _stringify(operation)
        result_text = _stringify(result)
        size = len(op_text) + len(result_text) + 16
        slot = _Slot(operation=op_text, result=result, size_chars=size)
        self._entries.append(slot)
        self._total_chars += size
        if len(result_text) > RESULT_RENDER_LIMIT:
            self._verbose[len(self._entries) - 1] = result_text

    def verbose_result(self, index: int) -> str | None:
        return self._verbose.get(index)

    def _is_context_full(self) -> bool:
        return (
            len(self._entries) > ENTRY_LIMIT
            or self._total_chars > CHAR_LIMIT
        )

    def compact_context(
        self,
        summarizer: Callable[[list[tuple[str, Any]]], str] | None = None,
        window: int = COMPACT_WINDOW,
    ) -> None:
        while self._is_context_full() and len(self._entries) > window:
            window_entries = list(self._entries[:window])
            payload = [(s.operation, s.result) for s in window_entries]
            if summarizer is None:
                summary = (
                    f"{COMPACT_FALLBACK_PREFIX} dropped {window} entries"
                )
            else:
                try:
                    summary = summarizer(payload)
                except Exception as exc:
                    summary = (
                        f"{COMPACT_FALLBACK_PREFIX} summarizer error: {exc}"
                    )
            self._entries = self._entries[window:]
            for entry in window_entries:
                self._total_chars -= entry.size_chars
            new_verbose: dict[int, str] = {}
            for old_idx, text in self._verbose.items():
                if old_idx >= window:
                    new_verbose[old_idx - window + 1] = text
            self._verbose = new_verbose
            placeholder = _Slot(
                operation="compact",
                result=summary,
                size_chars=len(summary) + 32,
            )
            self._entries.insert(0, placeholder)
            self._total_chars += placeholder.size_chars

    def dump(self) -> str:
        lines: list[str] = []
        for slot in self._entries:
            op_text = _truncate(slot.operation, RESULT_RENDER_LIMIT)
            result_text = _stringify(slot.result)
            if not _is_files_read(slot.operation):
                result_text = _truncate(result_text, RESULT_RENDER_LIMIT)
            lines.append(f"{op_text}: {result_text}\n---")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._entries)
