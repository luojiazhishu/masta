from __future__ import annotations

import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from tamarin_infra import GoalSelector, TamarinClient

from src.model.tamarin_result import TamarinResult
from src.model.verdict import TamarinVerdict


_VERDICT_LINE_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"\((?:all-traces|exists-trace)\)\s*:\s*"
    r"(?P<verdict>verified|falsified|analysis incomplete)"
    r"(?:[^()\n]*\((?P<steps>\d+)\s*steps?\))?",
    re.MULTILINE,
)

_LEMMA_DECL_RE = re.compile(
    r"(?m)^[ \t]*(?:all-traces[ \t]+|exists-trace[ \t]+)?lemma[ \t]+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)

EXECUTABLE_LEMMA_NAME = "executable"


def extract_safety_lemma_names(spec_text: str) -> list[str]:
    """Return safety-property lemma names from a Tamarin spec, in source order.

    The mandatory ``executable`` lemma (whose trace is consumed by action-block
    extraction rather than safety verification) is excluded. Duplicate names —
    which Tamarin itself rejects, but a malformed LLM output may contain — are
    deduplicated, keeping the first occurrence.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for match in _LEMMA_DECL_RE.finditer(spec_text):
        name = match.group("name")
        if name == EXECUTABLE_LEMMA_NAME:
            continue
        if name in seen:
            continue
        seen.add(name)
        ordered.append(name)
    return ordered


def extract_lemma_definition(spec_text: str, lemma_name: str) -> str | None:
    """Return the full source text of the named lemma from a Tamarin spec.

    The returned slice starts at the leftmost column of the matched ``lemma``
    declaration line (including any preceding ``all-traces`` / ``exists-trace``
    keyword) and ends at the closing double quote that terminates the
    formula body. Any attributes between ``lemma <name>`` and the opening
    quote (e.g. ``[reuse]`` or ``[heuristic={s}]``) are preserved.

    Returns ``None`` when the lemma name is absent, when no opening quote
    follows the declaration, or when the closing quote cannot be located.
    The matcher requires the declaration to begin at column zero modulo
    indentation; commented-out lemma blocks and lemma identifiers nested
    inside other declarations are intentionally not recognized.
    """
    if not spec_text or not lemma_name:
        return None

    target_match: re.Match[str] | None = None
    for match in _LEMMA_DECL_RE.finditer(spec_text):
        if match.group("name") == lemma_name:
            target_match = match
            break
    if target_match is None:
        return None

    start = target_match.start()
    after_decl = target_match.end()

    quote_open = spec_text.find('"', after_decl)
    if quote_open < 0:
        return None
    quote_close = spec_text.find('"', quote_open + 1)
    if quote_close < 0:
        return None

    decl_line_start = spec_text.rfind("\n", 0, start) + 1
    return spec_text[decl_line_start : quote_close + 1]


def _normalize_executable_lemma_name(
    spec_text: str, canonical: str = EXECUTABLE_LEMMA_NAME
) -> str:
    """Coerce any case-variant of the executable lemma declaration to the
    canonical lowercase name expected by ``verify_executable``.

    LLMs occasionally emit ``lemma Executable:`` or ``lemma EXECUTABLE:``
    even when the prompt template shows lowercase. Tamarin lemma names
    are case-sensitive, so the fixed lookup of ``executable`` would
    fail with ``"Configured target lemma was not found"``. This rewrite
    targets only the executable lemma declaration line and preserves
    indentation, the optional ``all-traces`` / ``exists-trace`` qualifier
    and any trailing attribute block (``[reuse]`` etc.).
    """
    pattern = re.compile(
        r"(?im)^(?P<prefix>\s*(?:all-traces[ \t]+|exists-trace[ \t]+)?lemma[ \t]+)"
        + re.escape(canonical)
        + r"\b"
    )
    return pattern.sub(lambda m: m.group("prefix") + canonical, spec_text)


def _default_goal_selector(
    grouped_goals: dict[str, list[str]],
    candidates: list[dict[str, Any]],
    state: dict[str, Any],
    lemma_name: str,
    step_index: int,
) -> str | None:
    for candidate in candidates:
        goal_id = str(candidate.get("id") or candidate.get("rule") or "").strip()
        if goal_id:
            return goal_id
    return None


class TamarinRunner:
    def __init__(
        self,
        timeout_seconds: int,
        tamarin_binary: str = "tamarin-prover",
        goal_selector: GoalSelector | None = None,
        quit_on_warning: bool = True,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._tamarin_binary = tamarin_binary
        self._goal_selector: GoalSelector = goal_selector or _default_goal_selector
        # ``--quit-on-warning`` tells tamarin-prover to abort on
        # wellformedness warnings before attempting any proof. That is
        # the right default for agent-produced theories (we want them
        # to fail fast on broken syntax), but baseline models in the
        # dataset can carry author-known warnings (e.g. partial
        # message-derivation patterns) that still admit a real proof
        # — for those we want the prover to continue regardless. Pass
        # ``quit_on_warning=False`` from prepare_baseline_steps and
        # any other "trust-the-baseline" caller.
        self._quit_on_warning = quit_on_warning

    def check_wellformed(self, spec_text: str) -> TamarinResult:
        return self._run(spec_text, lemma_name=None, prove=False)

    def verify_executable(
        self, spec_text: str, lemma_name: str = EXECUTABLE_LEMMA_NAME
    ) -> TamarinResult:
        normalized = _normalize_executable_lemma_name(spec_text, lemma_name)
        return self._run(normalized, lemma_name=lemma_name, prove=True)

    def verify_lemma(self, spec_text: str, lemma_name: str) -> TamarinResult:
        return self._run(spec_text, lemma_name=lemma_name, prove=True)

    def extract_action_blocks(
        self,
        spec_text: str,
        *,
        lemma_name: str = "executable",
        output_dir: Path | None = None,
        yaml_file_name: str = "action_blocks.yaml",
        extra_args: list[str] | None = None,
    ) -> list[dict[str, object]] | None:
        """Run Tamarin against ``spec_text`` and derive action blocks.

        Wraps :py:meth:`tamarin_infra.client.TamarinClient.extract_action_blocks`.
        Writes ``spec_text`` to a temporary spthy file, invokes the underlying
        proof + extraction pipeline against the named lemma, and optionally
        serializes the result to ``output_dir/yaml_file_name``. Returns the
        action-block list on success, or ``None`` when verification, parsing,
        or extraction fails. Failure cases are not raised so the caller can
        keep the surrounding pipeline going (consistent with :meth:`verify_lemma`).
        """
        if not spec_text:
            return None

        with tempfile.TemporaryDirectory() as tmpdir:
            spec_path = Path(tmpdir) / "theory.spthy"
            spec_path.write_text(spec_text, encoding="utf-8")

            try:
                with TamarinClient(
                    mode="local",
                    headless=True,
                    workdir=tmpdir,
                    timeout=float(self._timeout_seconds),
                    tamarin_binary=self._tamarin_binary,
                ) as client:
                    blocks = client.extract_action_blocks(
                        model_file=str(spec_path),
                        lemma_name=lemma_name,
                        output_dir=output_dir,
                        yaml_file_name=yaml_file_name,
                        extra_args=extra_args,
                        timeout=float(self._timeout_seconds),
                    )
            except Exception:
                return None

        if not isinstance(blocks, list):
            return None
        return blocks

    def _run(
        self,
        spec_text: str,
        lemma_name: str | None,
        prove: bool,
    ) -> TamarinResult:
        """Drive the ``tamarin-prover`` CLI directly against ``spec_text``.

        Writes the spec into a temp file, runs ``tamarin-prover
        --quit-on-warning [--prove=<lemma>] <theory.spthy>``, and parses
        the per-lemma summary line out of stdout to derive the verdict.
        ``--quit-on-warning`` makes the prover bail on wellformedness
        issues instead of silently scoring them as falsifications, so
        parse / structural errors surface in stderr where the agent
        prompts already key on them.
        """
        start = time.monotonic()
        stderr = ""
        stdout = ""
        verdict = TamarinVerdict.FALSIFIED

        with tempfile.TemporaryDirectory() as tmpdir:
            spec_path = Path(tmpdir) / "theory.spthy"
            spec_path.write_text(spec_text, encoding="utf-8")

            argv: list[str] = [self._tamarin_binary, "--auto-sources"]
            if self._quit_on_warning:
                argv.append("--quit-on-warning")
            if prove and lemma_name:
                argv.append(f"--prove={lemma_name}")
            argv.append(str(spec_path))

            try:
                completed = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                wall = time.monotonic() - start
                return TamarinResult(
                    verdict=TamarinVerdict.FALSIFIED,
                    lemma_name=lemma_name,
                    stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
                    stderr=f"timeout after {self._timeout_seconds}s",
                    wall_time_seconds=wall,
                    raw_trace=None,
                )
            except FileNotFoundError as exc:
                wall = time.monotonic() - start
                return TamarinResult(
                    verdict=TamarinVerdict.FALSIFIED,
                    lemma_name=lemma_name,
                    stdout="",
                    stderr=f"tamarin binary not found: {exc}",
                    wall_time_seconds=wall,
                    raw_trace=None,
                )

            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            steps: int | None = None
            if not prove:
                verdict = (
                    TamarinVerdict.VERIFIED
                    if completed.returncode == 0
                    else TamarinVerdict.FALSIFIED
                )
            else:
                assert lemma_name is not None
                verdict, steps = _parse_verdict(stdout, lemma_name)

        wall = time.monotonic() - start
        return TamarinResult(
            verdict=verdict,
            lemma_name=lemma_name,
            stdout=stdout,
            stderr=stderr,
            wall_time_seconds=wall,
            raw_trace=None,
            steps=steps,
        )


def _parse_verdict(
    stdout: str, lemma_name: str
) -> tuple[TamarinVerdict, int | None]:
    """Pick the per-lemma verdict line out of ``tamarin-prover`` stdout.

    The summary block contains lines shaped like
    ``<name> (all-traces): verified (N steps)`` /
    ``<name> (exists-trace): falsified - found trace ...``;
    we match the named lemma and return ``(verdict, steps)``. ``steps``
    is the integer in ``(N steps)`` when present (typically populated
    on ``verified`` lines), otherwise ``None``. Anything else (analysis
    incomplete, missing line) collapses to FALSIFIED with ``steps=None``.
    """
    for match in _VERDICT_LINE_RE.finditer(stdout):
        if match.group("name") != lemma_name:
            continue
        steps_raw = match.group("steps")
        steps = int(steps_raw) if steps_raw is not None else None
        if match.group("verdict") == "verified":
            return TamarinVerdict.VERIFIED, steps
        return TamarinVerdict.FALSIFIED, steps
    return TamarinVerdict.FALSIFIED, None


def extract_m1_action_blocks(
    runner: Any,
    work_dir: Path,
    output_dir: Path,
    yaml_filename: str = "action_blocks.yaml",
) -> Path | None:
    """Run TamarinRunner.extract_action_blocks against the final
    ``model.spthy`` to produce the M1 evaluation YAML.

    Returns the YAML path on success, ``None`` when extraction fails.
    """
    spec_path = work_dir / "model.spthy"
    if not spec_path.exists():
        return None
    spec_text = spec_path.read_text(encoding="utf-8")
    output_dir.mkdir(parents=True, exist_ok=True)
    blocks = runner.extract_action_blocks(
        spec_text,
        lemma_name="executable",
        output_dir=output_dir,
        yaml_file_name=yaml_filename,
    )
    if blocks is None:
        return None
    return output_dir / yaml_filename
