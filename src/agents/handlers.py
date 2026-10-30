from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


def make_files_handler(
    *,
    work_dir: Path,
    write_whitelist: Iterable[str],
    role: str,
    read_extra_dirs: Mapping[str, Path] | None = None,
) -> Callable[[dict[str, Any]], Any]:
    """Create a `files` capability handler scoped to `work_dir`.

    Reads default to ``work_dir``; supplying an optional ``subdir`` field
    routes the read into one of ``read_extra_dirs`` (read-only mirrors
    such as the local Tamarin manual under ``data/tamarin/``). Writes
    always target ``work_dir`` and are limited to ``write_whitelist`` (an
    empty iterable means writes are forbidden — used by the Arbiter).
    """

    allowed = frozenset(write_whitelist)
    extra_dirs: dict[str, Path] = dict(read_extra_dirs or {})
    work_dir.mkdir(parents=True, exist_ok=True)

    def _safe_path(root: Path, name: str) -> Path:
        if not name or "/" in name or ".." in name:
            raise ValueError(f"invalid filename {name!r}")
        return root / name

    def _resolve_read_root(subdir: Any) -> tuple[Path | None, dict | None]:
        if subdir in (None, ""):
            return work_dir, None
        if not isinstance(subdir, str) or subdir not in extra_dirs:
            return None, {"error": f"unknown subdir {subdir!r}"}
        return extra_dirs[subdir], None

    def handler(args: dict[str, Any]) -> Any:
        action = args.get("action")
        filename = str(args.get("filename") or "")
        if action == "read":
            root, err = _resolve_read_root(args.get("subdir"))
            if err is not None:
                return err
            target = _safe_path(root, filename)
            if not target.exists():
                return {"error": f"artifact not found: {filename}"}
            text = target.read_text(encoding="utf-8")
            n_chars = len(text)
            n_lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
            return (
                f"Successfully read file {filename!r} from the workspace "
                f"({n_chars} chars / {n_lines} lines). "
                f"The full content of {filename!r} is shown below:\n"
                f"---BEGIN {filename}---\n"
                f"{text}\n"
                f"---END {filename}---"
            )
        if action == "write":
            if not allowed:
                return {"error": f"role {role!r} has no write permission"}
            if filename not in allowed:
                return {
                    "error": (
                        f"filename {filename!r} not in write whitelist "
                        f"for role {role!r}: {sorted(allowed)}"
                    )
                }
            payload = args.get("payload", "")
            if not isinstance(payload, str):
                return {"error": "payload must be a string"}
            payload = _strip_code_fence(payload)
            target = _safe_path(work_dir, filename)
            target.write_text(payload, encoding="utf-8")
            n_bytes = len(payload.encode("utf-8"))
            # Return a deterministic confirmation message (no absolute
            # paths) so the observation does not poison the LLM-call
            # cache key with the run-specific work_dir.
            return (
                f"Successfully wrote {n_bytes} bytes to file {filename!r} "
                f"in the workspace. The file {filename!r} now stores "
                f"exactly the payload you supplied in this turn."
            )
        if action == "exists":
            root, err = _resolve_read_root(args.get("subdir"))
            if err is not None:
                return err
            return {"exists": _safe_path(root, filename).exists()}
        return {"error": f"unknown files action {action!r}"}

    return handler


def _strip_code_fence(payload: str) -> str:
    """Remove a leading ```<lang>\\n / trailing ``` Markdown code fence
    if the LLM wrapped the file body in one. Tamarin / YAML / Markdown
    files all break when an artefact starts with backticks; this is a
    provider-side belt to complement the prompt-level "no fences" rule.
    Payloads without a fence pass through unchanged.
    """
    text = payload.lstrip()
    if not text.startswith("```"):
        return payload
    newline = text.find("\n")
    if newline == -1:
        return payload  # malformed — keep as-is so the user sees raw bytes
    body = text[newline + 1:]
    stripped = body.rstrip()
    if stripped.endswith("```"):
        body = stripped[:-3]
    return body


def make_tamarin_handler(
    runner: Any,
    *,
    work_dir: Path,
) -> Callable[[dict[str, Any]], Any]:
    """Adapt a TamarinRunner / TamarinCapability into the agent surface.

    Allowed actions: ``well_formed`` / ``verify_executable`` / ``verify_lemma``.
    Each action takes a ``filename`` (relative to ``work_dir``); the
    handler reads the file from disk and feeds its text to Tamarin, so
    the agent never has to inline the entire theory body in its prompt.
    Action-block extraction is intentionally absent.
    """

    def _summarize(result: Any) -> dict[str, Any]:
        # ``wall_time_seconds`` is intentionally NOT in the agent-facing
        # observation: it varies between runs and would invalidate the
        # LLM-call cache for every downstream turn. The same field is
        # still emitted to events.jsonl via ``EventLogger.log_tamarin_call``.
        verdict = getattr(result, "verdict", None)
        return {
            "verdict": getattr(verdict, "value", str(verdict)),
            "lemma_name": getattr(result, "lemma_name", None),
            "stdout": (getattr(result, "stdout", "") or "")[:600],
            "stderr": (getattr(result, "stderr", "") or "")[:300],
            "raw_trace": (getattr(result, "raw_trace", "") or "")[:600],
        }

    def _load_spec(filename: str) -> tuple[str | None, dict[str, Any] | None]:
        if not filename or "/" in filename or ".." in filename:
            return None, {"error": f"invalid filename {filename!r}"}
        target = work_dir / filename
        if not target.exists():
            return None, {"error": f"artifact not found: {filename}"}
        return target.read_text(encoding="utf-8"), None

    def handler(args: dict[str, Any]) -> Any:
        action = args.get("action")
        filename = str(args.get("filename") or "")
        spec_text, err = _load_spec(filename)
        if err is not None:
            return err
        assert spec_text is not None
        if action == "well_formed":
            return _summarize(runner.check_wellformed(spec_text))
        if action == "verify_executable":
            return _summarize(runner.verify_executable(spec_text))
        if action == "verify_lemma":
            lemma = args.get("lemma_name")
            if not isinstance(lemma, str) or not lemma:
                return {"error": "lemma_name is required for verify_lemma"}
            return _summarize(runner.verify_lemma(spec_text, lemma))
        return {"error": f"unknown tamarin action {action!r}"}

    return handler
