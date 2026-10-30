from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Final

from src.agents.base.context import Context
from src.agents.prompts.capability import render_capabilities_section
from src.console import (
    BOLD,
    DIM,
    GREEN,
    RED,
    YELLOW,
    agent_prefix,
    log,
    paint,
    pending,
)
from src.llm.base import LLMResponse
from src.logging_ import EventLogger


_ACTION_DETAIL_KEYS: Final[tuple[str, ...]] = (
    "filename",
    "subdir",
    "lemma_name",
    "task_label",
    "target_role",
    "target_artifact",
    "problem_type",
    "reason",
)


class _StaticEmitter:
    """Pending lookalike that prints two static lines instead of the
    animated dot ticker. Used by the Arbiter so its own ``→`` line does
    not collide with the sub-agent console output that the
    ``scheduler.*`` capability call triggers.
    """

    def __init__(self, start_message: str) -> None:
        log(start_message)

    def done(self, final_message: str) -> None:
        log(final_message)


_GLOBAL_TRACE_COUNTERS: dict[str, int] = {}


def _next_global_trace_seq(trace_dir: "Path") -> int:
    """Return the next chronological sequence number for ``trace_dir``.

    Cross-agent: all three roles writing into the same ``trace_dir``
    share one counter so the resulting filenames sort by emission
    order. On first use for a given directory we seed the counter from
    any existing files matching ``NNN_<role>_turn_NN.json`` so a
    restart with leftover traces continues numbering instead of
    overwriting.
    """
    key = str(trace_dir.resolve())
    current = _GLOBAL_TRACE_COUNTERS.get(key)
    if current is None:
        current = 0
        if trace_dir.exists():
            for path in trace_dir.glob("[0-9][0-9][0-9]_*_turn_*.json"):
                head = path.name.split("_", 1)[0]
                try:
                    num = int(head)
                except ValueError:
                    continue
                if num > current:
                    current = num
    current += 1
    _GLOBAL_TRACE_COUNTERS[key] = current
    return current


def _describe_action(action: dict[str, Any]) -> str:
    """Render one ReAct action as ``cap[.subaction] key=val ...`` for the
    per-turn console log. ``cap`` is the capability; ``subaction`` is the
    inner ``args.action`` dispatch (read/write/verify_lemma/...). Only a
    short list of routinely useful args is surfaced; payload-heavy fields
    like ``payload`` / ``spec_text`` / ``brief`` are omitted because the
    full action is preserved verbatim in the per-turn trace JSON.
    """
    cap = action.get("capability", "?")
    args = action.get("args", {})
    if not isinstance(args, dict):
        return cap
    sub = args.get("action") or ""
    head = f"{cap}.{sub}" if sub else cap
    detail_parts: list[str] = []
    for key in _ACTION_DETAIL_KEYS:
        value = args.get(key)
        if value:
            detail_parts.append(f"{key}={value}")
    if detail_parts:
        return f"{head} " + " ".join(detail_parts)
    return head


def _describe_result(action: dict[str, Any], result: Any) -> str:
    """Render an ``__execute_step`` result as a one-line summary for the
    per-turn console log. Errors surface verbatim; otherwise we project
    each capability's result into a few key fields so payload bodies
    (file content, spec text, raw stdout) stay out of the console.
    """
    if isinstance(result, dict) and "error" in result:
        return f"error: {str(result['error'])[:200]}"
    cap = action.get("capability")
    args = action.get("args", {})
    sub = args.get("action") if isinstance(args, dict) else None
    if cap == "files":
        if sub == "read":
            if isinstance(result, str):
                return f"read {len(result)} chars / {result.count(chr(10)) + 1} lines"
            return repr(result)[:120]
        if sub == "write":
            fname = args.get("filename", "?")
            payload = args.get("payload")
            if isinstance(payload, str):
                return f"wrote {fname} ({len(payload.encode('utf-8'))} bytes)"
            if isinstance(result, dict):
                return f"wrote {fname} ({result.get('bytes', 0)} bytes)"
            return f"wrote {fname}"
        if sub == "exists":
            return f"exists={bool(result)}"
    if cap == "tamarin" and isinstance(result, dict):
        verdict = result.get("verdict") or "?"
        lemma = result.get("lemma_name") or ""
        suffix = f" lemma={lemma}" if lemma else ""
        return f"verdict={verdict}{suffix}"
    if cap == "scheduler" and isinstance(result, dict):
        if "summary" in result and isinstance(result["summary"], dict):
            summary = result["summary"]
            if summary.get("ended"):
                return f"summary ended reason={summary.get('end_reason', '')!r}"
            concern = summary.get("concern") or ""
            if concern:
                return f"summary concern={concern[:160]!r}"
            return "summary in_progress"
        if result.get("started"):
            tr = result.get("target_role", "?")
            ta = result.get("target_artifact", "?")
            return f"started target={tr}/{ta}"
    if cap == "end":
        reason = args.get("reason", "") if isinstance(args, dict) else ""
        return f"end reason={reason!r}"
    text = str(result) if result is not None else "None"
    return text[:200]


OUTPUT_PROTOCOL: Final[str] = """
ReAct contract — every turn returns exactly ONE JSON object of shape:

{"action": {"capability": "<name>", "args": {...}},
 "summary": {"did":               "<one short sentence>",
             "last_verification": "<one short sentence or empty string>",
             "outstanding":       "<one short sentence or empty string>",
             "concern":           "<one short sentence or empty string>"}}

Constraints:
- Use the model's native reasoning / thinking channel for your
  step-by-step deliberation. The host captures that channel and stores
  it in the per-turn trace. The JSON body returned to the host MUST
  contain ONLY the chosen action and the summary; do NOT add a
  `thought` field. (If you produce one anyway it will be accepted but
  duplicates the work the native reasoning already did.)
- Exactly one action per turn. The legacy `operations` array is rejected.
- `summary.did` MUST be one short sentence stating what this turn did
  (the action you ran and its observable effect).
- `summary.last_verification` MUST be filled on every turn that ran a
  tamarin tool call: write the verdict plus one short clause of the
  most relevant stderr / lemma_name detail (e.g. "verified",
  "falsified, stderr=Maude 3.2 not in supported list",
  "verify_lemma secrecy: falsified, real counterexample"). On turns
  that ran no tool, leave the empty string.
- `summary.outstanding` MUST be one short sentence listing what is
  still missing before the current stage can end (e.g. "still need
  to add lemma for session-key secrecy; verify_executable has not
  been run yet"). Use the empty string when nothing is outstanding.
- `summary.concern` MUST be one short sentence describing any
  self-evaluated worry about the current state (format ambiguity,
  repeated tamarin parse error, missing prerequisite, ...). Use the
  empty string when there is no concern. Be honest — the Arbiter
  reads `did` / `last_verification` / `outstanding` / `concern`
  together with the host-supplied `turn_log` to decide whether to
  advance, revise, or terminate.
- `## CONTEXT` carries your previous (action, observation) pairs;
  build on that history rather than restating fixed plans.
- To finish the current task emit
  `{"capability": "end", "args": {"reason": "<text>"}}`
  as the action.
""".strip()


class BaseAgent:
    """Skeleton for the three MASTA roles.

    Each subclass populates ``role_desp`` / ``responsibilities_desp`` and
    registers its capability handlers via ``__capabilities__``. The prompt is
    composed from six sections in the order
    ``OUTPUT PROTOCOL / ROLE DESP / CAP DESP / RESPONSIBILITIES DESP /
    CONTEXT / CURRENT TASK``. The capability section is derived from the
    keys of ``__capabilities__`` so a role's prompt and its dispatcher
    cannot drift apart.
    """

    def __init__(
        self,
        name: str,
        *,
        role_desp: str = "",
        responsibilities_desp: str = "",
        trace_dir: "Path | None" = None,
    ) -> None:
        self.name = name
        self.role_desp = role_desp
        self.responsibilities_desp = responsibilities_desp
        self.context = Context()
        self.current_task: str = ""
        self.__capabilities__: dict[str, Callable[[dict[str, Any]], Any]] = {}
        self._llm_caller: Callable[[str], LLMResponse] | None = None
        self.trace_dir = trace_dir
        self._trace_seq = 0
        self._turn = 0
        self._ended = False
        self._end_reason = ""
        self._events = EventLogger()

    @property
    def capabilities_desp(self) -> str:
        names = [n for n in self.__capabilities__.keys() if n != "end"]
        return render_capabilities_section(names)

    def register_capability(
        self,
        name: str,
        handler: Callable[[dict[str, Any]], Any],
    ) -> None:
        self.__capabilities__[name] = handler

    def attach_llm(self, caller: Callable[[str], LLMResponse]) -> None:
        """Inject the LLM call site. ``caller(prompt) -> LLMResponse``
        with both the chat ``content`` and any native ``thinking``
        channel populated by the underlying client.
        """
        self._llm_caller = caller

    def __prompt(self) -> str:
        sections = [
            ("OUTPUT PROTOCOL", OUTPUT_PROTOCOL),
            ("ROLE DESP", self.role_desp.strip()),
            ("CAP DESP", self.capabilities_desp.strip()),
            ("RESPONSIBILITIES DESP", self.responsibilities_desp.strip()),
            ("CONTEXT", self.context.dump().strip()),
            ("CURRENT TASK", self.current_task.strip()),
        ]
        out: list[str] = [f"# Agent: {self.name}"]
        for heading, body in sections:
            out.append(f"\n## {heading}\n")
            out.append(body if body else "(empty)")
        return "\n".join(out).rstrip() + "\n"

    def __execute_step(
        self, step: dict[str, Any]
    ) -> tuple[Any, bool, str]:
        """Run one ReAct (thought, action) step.

        Returns ``(result, ended, end_reason)``. The (thought, action)
        pair is recorded in CONTEXT as a single JSON entry alongside the
        observation.
        """
        thought = step["thought"]
        action = step["action"]
        cap = action.get("capability")
        raw_args = action.get("args", {})
        args = raw_args if isinstance(raw_args, dict) else {}
        op_record = json.dumps(
            {"thought": thought, "action": action},
            ensure_ascii=False,
        )
        if cap == "end":
            reason = args.get("reason", "") if isinstance(args, dict) else ""
            self.context.add(op_record, {"end_reason": reason})
            return None, True, str(reason)
        handler = self.__capabilities__.get(cap)
        if handler is None:
            err = {"error": f"unknown capability {cap!r}"}
            self.context.add(op_record, err)
            return err, False, ""
        try:
            result = handler(args)
        except Exception as exc:
            result = {"error": str(exc)}
        self.context.add(op_record, result)
        return result, False, ""

    def _parse_step(self, raw: str) -> dict[str, Any]:
        """Parse one ReAct step from the LLM raw response.

        Accepts exactly one top-level JSON object with key ``action``
        (object with ``capability``); ``thought`` is optional now that
        the native reasoning channel carries the model's deliberation.
        Returns ``{"thought": ..., "action": ..., "summary": ...}`` on
        success or ``{"error": "..."}`` on failure.
        """
        text = self._extract_json_text(raw)
        try:
            objects = self._decode_top_level_objects(text)
        except (TypeError, ValueError) as exc:
            return {"error": f"invalid json: {exc}"}
        if not objects:
            return {"error": "no JSON object found in response"}
        if len(objects) > 1:
            return {
                "error": (
                    "exactly one JSON object per turn (one ReAct step); "
                    f"got {len(objects)}"
                )
            }
        obj = objects[0]
        if not isinstance(obj, dict):
            return {"error": "top-level value must be a JSON object"}
        thought = obj.get("thought")
        if thought is not None and not isinstance(thought, str):
            return {"error": "'thought' must be a string when present"}
        action = obj.get("action")
        if not isinstance(action, dict) or "capability" not in action:
            return {
                "error": "missing 'action' object with 'capability' key"
            }
        raw_summary = obj.get("summary")
        summary = raw_summary if isinstance(raw_summary, dict) else {}
        return {
            "thought": (thought or "").strip(),
            "action": action,
            "summary": summary,
        }

    @staticmethod
    def _decode_top_level_objects(text: str) -> list[Any]:
        """Decode a sequence of whitespace-separated top-level JSON values.

        Returns the list of decoded values in source order. Raises a
        ``json.JSONDecodeError`` (a ``ValueError``) if the leading
        non-whitespace bytes cannot be decoded; trailing prose is
        ignored once at least one value has been recovered.
        """
        decoder = json.JSONDecoder()
        objects: list[Any] = []
        pos = 0
        length = len(text)
        while pos < length and text[pos].isspace():
            pos += 1
        while pos < length:
            try:
                obj, end = decoder.raw_decode(text, pos)
            except json.JSONDecodeError:
                if not objects:
                    raise
                break
            objects.append(obj)
            pos = end
            while pos < length and text[pos].isspace():
                pos += 1
        return objects

    def _dump_trace(
        self,
        *,
        prompt: str,
        raw: str,
        parsed: dict[str, Any],
        thinking: str = "",
    ) -> None:
        """Persist one LLM exchange (prompt + raw response + parse
        outcome + native thinking channel) under
        ``self.trace_dir/<agent>_turn_NN.json``. No-ops if ``trace_dir``
        was not supplied at construction; the only goal of this artefact
        is post-mortem inspection of what the model actually returned.
        """
        if self.trace_dir is None:
            return
        self._trace_seq += 1
        try:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            global_seq = _next_global_trace_seq(self.trace_dir)
            path = self.trace_dir / (
                f"{global_seq:03d}_{self.name}_turn_"
                f"{self._trace_seq:02d}.json"
            )
            payload: dict[str, Any] = {
                "agent": self.name,
                "turn": self._trace_seq,
                "global_seq": global_seq,
                "current_task": self.current_task,
                "prompt": prompt,
                "raw_response": raw,
                "thinking": thinking,
            }
            if "error" in parsed:
                payload["parse_error"] = parsed
            else:
                payload["thought"] = parsed.get("thought")
                payload["action"] = parsed.get("action")
            path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            # Trace dumping must never break the agent loop; report and
            # continue.
            log(
                f"{agent_prefix(self.name)} trace dump failed: "
                f"{paint(f'{type(exc).__name__}: {exc}', RED)}"
            )

    @staticmethod
    def _extract_json_text(raw: str) -> str:
        """Best-effort recovery of a JSON object from an LLM response.

        Strips a leading ```json / ``` fence (with optional language tag)
        and the matching trailing ```; whitespace-trims the rest. Combined
        with ``json.JSONDecoder().raw_decode``, the parser then tolerates
        any prose the model appended after the JSON body.
        """
        text = raw.strip()
        if text.startswith("```"):
            newline = text.find("\n")
            if newline != -1:
                text = text[newline + 1 :]
            else:
                text = text[3:]
            stripped = text.rstrip()
            if stripped.endswith("```"):
                text = stripped[:-3]
        return text.strip()

    def start_task(self, new_task: str) -> None:
        """Set the current task and reset the per-task end state.

        ``step`` consumes the task one turn at a time; ``work`` is a
        thin loop on top of ``step`` for callers that prefer the
        legacy "run to end" behaviour.
        """
        self.current_task = new_task
        self._turn = 0
        self._ended = False
        self._end_reason = ""
        first_line = (new_task.strip().splitlines() or [""])[0]
        log(
            f"{agent_prefix(self.name)} start "
            f"(task='{paint(first_line[:50], BOLD)}')"
        )

    def cancel(self, reason: str) -> dict[str, Any]:
        """Mark the agent as ended without running another LLM turn.

        Returns a synthetic summary so callers can record the
        cancellation in their observation channel uniformly with
        natural ``step`` outputs.
        """
        self._ended = True
        self._end_reason = reason
        log(
            f"{agent_prefix(self.name)} cancelled after {self._turn} turn(s) "
            f"reason={paint(repr(reason), YELLOW)}"
        )
        return {
            "did": "cancelled",
            "last_verification": "",
            "outstanding": "",
            "concern": "",
            "ended": True,
            "end_reason": reason,
        }

    def step(self) -> dict[str, Any]:
        """Run exactly one ReAct turn and return its summary.

        Summary keys: ``did`` (one-sentence statement of what this
        turn did), ``concern`` (self-evaluated worry, possibly empty
        string), ``ended`` (whether this turn was the agent's end
        action), ``end_reason`` (filled when ``ended`` is true).

        Calls after the agent has already ended are idempotent: they
        return the terminal summary without invoking the LLM.
        """
        if self._ended:
            return {
                "did": "",
                "last_verification": "",
                "outstanding": "",
                "concern": "",
                "ended": True,
                "end_reason": self._end_reason,
            }
        if self._llm_caller is None:
            raise RuntimeError(
                f"agent {self.name!r} has no LLM caller attached"
            )
        self._turn += 1
        prompt = self.__prompt()
        prefix = agent_prefix(self.name)
        arrow_out = paint("→", YELLOW)
        arrow_ok = paint("←", GREEN)
        arrow_err = paint("←", RED)
        # Arbiter never animates: its scheduler.* call drives the next
        # expert through ``_run_until_escalation`` which emits its own
        # console output, and a ticker dot animation on the same row
        # collides visually with sub-agent log lines.
        animate = self.name != "arbiter"

        def _emit(start_msg: str) -> "object":
            return pending(start_msg) if animate else _StaticEmitter(start_msg)

        # --- LLM call --------------------------------------------------
        llm_label = paint("llm", BOLD)
        ticker = _emit(f"{prefix} turn {self._turn} {arrow_out} {llm_label}")
        llm_started = time.monotonic()
        response = self._llm_caller(prompt)
        llm_elapsed = time.monotonic() - llm_started
        ticker.done(
            f"{prefix} turn {self._turn} {arrow_ok} {llm_label}  "
            f"{response.prompt_tokens}/{response.completion_tokens} tokens  "
            f"{paint(f'{llm_elapsed:.2f}s', DIM)}"
        )
        raw = response.content
        thinking = response.thinking or ""
        parsed = self._parse_step(raw)
        if "error" in parsed:
            log(
                f"{prefix} turn {self._turn} "
                f"{paint('parse_error', RED)}: "
                f"{parsed.get('error', '')[:120]}"
            )
            self._dump_trace(
                prompt=prompt, raw=raw, parsed=parsed, thinking=thinking
            )
            self.context.add("parse_error", parsed)
            return {
                "did": "parse_error",
                "last_verification": "",
                "outstanding": "",
                "concern": parsed.get("error", "")[:200],
                "ended": False,
                "end_reason": "",
            }
        cap = parsed["action"].get("capability", "?")
        thought_source = thinking or parsed["thought"]
        thought_text = " ".join(thought_source.split())
        if len(thought_text) > 120:
            thought_text = thought_text[:120] + "..."
        action_desc = _describe_action(parsed["action"])
        action_label = paint(action_desc, BOLD)
        self._dump_trace(
            prompt=prompt, raw=raw, parsed=parsed, thinking=thinking
        )

        # --- capability call ------------------------------------------
        ticker = _emit(f"{prefix} turn {self._turn} {arrow_out} {action_label}")
        cap_started = time.monotonic()
        result, ended, reason = self.__execute_step(parsed)
        cap_elapsed = time.monotonic() - cap_started
        result_brief = _describe_result(parsed["action"], result)
        is_error = (
            result_brief.startswith("error:")
            or "verdict=falsified" in result_brief
        )
        ticker.done(
            f"{prefix} turn {self._turn} "
            f"{arrow_err if is_error else arrow_ok} {action_label}  "
            f"{paint(result_brief, RED) if is_error else result_brief}  "
            f"{paint(f'{cap_elapsed:.2f}s', DIM)}"
        )
        if thought_text:
            log(f"{prefix} turn {self._turn}   {paint(f'↳ {thought_text}', DIM)}")
        self._events.log_event(
            stage="agent",
            event_type="agent_step",
            agent_role=self.name,
            turn=self._turn,
            capability=cap,
            action=action_desc,
            result=result_brief,
            ended=ended,
        )
        summary_obj = parsed.get("summary") or {}
        did_text = str(summary_obj.get("did") or "").strip()
        last_verification = str(
            summary_obj.get("last_verification") or ""
        ).strip()
        outstanding = str(summary_obj.get("outstanding") or "").strip()
        concern_text = str(summary_obj.get("concern") or "").strip()
        if not did_text:
            did_text = f"action={cap}"
        if ended:
            self._ended = True
            self._end_reason = reason
            log(
                f"{prefix} end after {self._turn} turn(s) "
                f"reason={paint(repr(reason), GREEN)}"
            )
        return {
            "did": did_text,
            "last_verification": last_verification,
            "outstanding": outstanding,
            "concern": concern_text,
            "ended": ended,
            "end_reason": reason if ended else "",
        }
