from __future__ import annotations

import json
import multiprocessing as mp
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import time
from collections.abc import Sequence

from experiments.checkpoint import CheckpointManager
from src.evaluation.output_layout import (
    write_lemma_outcome,
    write_weakness_lemma_outcome,
)
from src.logging_ import EventLogger
from src.model.protocol_prediction import ProtocolPrediction
from src.model.protocol_spec import ProtocolSpec
from src.model.verdict import TamarinVerdict
from src.orchestration import MastaSystem
from src.tamarin.runner import (
    extract_lemma_definition,
    extract_m1_action_blocks,
    extract_safety_lemma_names,
)


_events = EventLogger()


CHECKPOINT_FILENAME = "checkpoint.json"


SYSTEM_NAME = "ours"

ACTION_VARIANTS_DIRNAME = "action_variants"
WEAKNESS_VARIANTS_DIRNAME = "weakness_variants"


@dataclass
class ProtocolPaths:
    """Layout of a single protocol entry under the dataset root."""

    name: str
    document: Path
    action_variants: list[tuple[str, Path]]
    weakness_variants: list[tuple[str, Path]]

    @classmethod
    def from_directory(cls, root: Path) -> "ProtocolPaths":
        action_variants: list[tuple[str, Path]] = []
        if (root / ACTION_VARIANTS_DIRNAME).is_dir():
            for sub in sorted((root / ACTION_VARIANTS_DIRNAME).iterdir()):
                doc = sub / "document.txt"
                if doc.exists():
                    action_variants.append((sub.name, doc))
        weakness_variants: list[tuple[str, Path]] = []
        if (root / WEAKNESS_VARIANTS_DIRNAME).is_dir():
            for sub in sorted((root / WEAKNESS_VARIANTS_DIRNAME).iterdir()):
                doc = sub / "document.txt"
                if doc.exists():
                    weakness_variants.append((sub.name, doc))
        return cls(
            name=root.name,
            document=root / "document.txt",
            action_variants=action_variants,
            weakness_variants=weakness_variants,
        )


@dataclass
class ProtocolOutcome:
    name: str
    base_dir: Path
    base_action_blocks_yaml: Path | None
    base_lemma_verdicts: dict[str, str]
    action_variant_yamls: dict[str, Path | None]
    weakness_variant_outcomes: dict[str, dict[str, str]]


def _run_compute_with_timeout(
    compute_fn: Callable[[], Any],
    timeout_seconds: int | None,
) -> tuple[str, Any]:
    """Run ``compute_fn()`` in a forked child process under its own
    process group; on timeout, send SIGTERM to the whole group so any
    Tamarin grandchildren are reaped together with the agent loop.

    Returns one of:
    - ``("ok", value)`` — child finished and sent its return value
    - ``("err", repr)`` — child raised; ``repr`` is a short string
    - ``("timeout", None)`` — child still running after ``timeout_seconds``
    - ``("dead", None)`` — child exited without sending a result

    A ``timeout_seconds`` of ``None`` runs ``compute_fn`` in-process and
    always yields ``("ok", value)``.
    """
    if timeout_seconds is None:
        return ("ok", compute_fn())

    ctx = mp.get_context("fork")
    parent_conn, child_conn = ctx.Pipe(duplex=False)

    def _child_entry(conn: Any) -> None:
        try:
            os.setsid()
            conn.send(("ok", compute_fn()))
        except BaseException as exc:
            try:
                conn.send(("err", f"{type(exc).__name__}: {exc}"))
            except Exception:
                pass
        finally:
            conn.close()

    proc = ctx.Process(target=_child_entry, args=(child_conn,))
    proc.start()
    child_conn.close()
    proc.join(timeout_seconds)
    if proc.is_alive():
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        proc.join(5)
        if proc.is_alive():
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.join(2)
        return ("timeout", None)
    if parent_conn.poll():
        try:
            return parent_conn.recv()
        except EOFError:
            return ("dead", None)
    return ("dead", None)


def discover_protocols(dataset_dir: Path) -> list[ProtocolPaths]:
    if not dataset_dir.is_dir():
        return []
    out: list[ProtocolPaths] = []
    for sub in sorted(dataset_dir.iterdir()):
        if (sub / "document.txt").exists():
            out.append(ProtocolPaths.from_directory(sub))
    return out


def run_ours(
    *,
    dataset_dir: Path,
    artifacts_dir: Path,
    runner: Any,
    llm_caller: Callable[[str, str], str],
    extract_action_blocks: Callable[..., Any] | None = None,
    resume: bool = True,
    subdir_name: str | None = None,
    running_timeout_seconds_seconds: int | None = None,
) -> list[ProtocolOutcome]:
    """Drive MASTA over every protocol under ``dataset_dir``.

    For each protocol the coordinator runs once on the base document and
    once on each action / weakness variant. After the base run, the M1
    YAML is produced by ``runner.extract_action_blocks`` against the final
    ``model.spthy`` (override via ``extract_action_blocks`` for testing).

    Each document.txt within a protocol (the base case plus every action
    and weakness variant) is treated as one workflow input and gets its
    own checkpoint key (``"<protocol>:base"`` /
    ``"<protocol>:av:<id>"`` / ``"<protocol>:wv:<id>"``). On
    ``resume=True`` (default) any document already marked completed is
    replayed from its cached artefact references; on ``resume=False`` the
    checkpoint file is deleted and every document runs from scratch.
    """

    extractor = extract_action_blocks or (
        lambda **kw: extract_m1_action_blocks(runner=runner, **kw)
    )

    artifacts_dir = artifacts_dir / (subdir_name or SYSTEM_NAME)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    checkpoint_path = artifacts_dir / CHECKPOINT_FILENAME
    if not resume and checkpoint_path.exists():
        checkpoint_path.unlink()
    checkpoint = CheckpointManager(checkpoint_path)

    outcomes: list[ProtocolOutcome] = []
    protocols = discover_protocols(dataset_dir)
    print(f"[ours] discovered {len(protocols)} protocols under {dataset_dir}")
    _events.log_event(
        stage="run_ours",
        event_type="batch_start",
        protocol_count=len(protocols),
        dataset_dir=str(dataset_dir),
        artifacts_dir=str(artifacts_dir),
        resume=resume,
    )
    for protocol_index, protocol in enumerate(protocols, start=1):
        print(
            f"[ours] ({protocol_index}/{len(protocols)}) protocol "
            f"{protocol.name}"
        )
        _events.log_event(
            stage="protocol",
            event_type="protocol_start",
            protocol=protocol.name,
            index=protocol_index,
            total=len(protocols),
        )
        protocol_started = time.monotonic()
        base_dir = artifacts_dir / protocol.name / "base"
        try:
            m1_yaml, base_verdicts = _process_base(
                protocol=protocol,
                base_dir=base_dir,
                runner=runner,
                llm_caller=llm_caller,
                extractor=extractor,
                checkpoint=checkpoint,
                resume=resume,
                running_timeout_seconds_seconds=running_timeout_seconds_seconds,
            )
        except Exception as exc:
            print(
                f"[ours]   base FAILED ({type(exc).__name__}: {exc}); "
                "continuing with empty M1/M3 inputs"
            )
            m1_yaml, base_verdicts = None, {}

        av_yamls: dict[str, Path | None] = {}
        for variant_id, doc_path in protocol.action_variants:
            variant_dir = (
                artifacts_dir / protocol.name / ACTION_VARIANTS_DIRNAME / variant_id
            )
            try:
                av_yamls[variant_id] = _process_action_variant(
                    protocol_name=protocol.name,
                    variant_id=variant_id,
                    doc_path=doc_path,
                    variant_dir=variant_dir,
                    runner=runner,
                    llm_caller=llm_caller,
                    extractor=extractor,
                    checkpoint=checkpoint,
                    resume=resume,
                    running_timeout_seconds_seconds=running_timeout_seconds_seconds,
                )
            except Exception as exc:
                print(
                    f"[ours]   av/{variant_id} FAILED "
                    f"({type(exc).__name__}: {exc}); skipping this variant"
                )
                av_yamls[variant_id] = None

        wv_outcomes: dict[str, dict[str, str]] = {}
        for variant_id, doc_path in protocol.weakness_variants:
            variant_dir = (
                artifacts_dir / protocol.name / WEAKNESS_VARIANTS_DIRNAME / variant_id
            )
            try:
                wv_outcomes[variant_id] = _process_weakness_variant(
                    protocol_name=protocol.name,
                    variant_id=variant_id,
                    doc_path=doc_path,
                    variant_dir=variant_dir,
                    runner=runner,
                    llm_caller=llm_caller,
                    checkpoint=checkpoint,
                    resume=resume,
                    running_timeout_seconds_seconds=running_timeout_seconds_seconds,
                )
            except Exception as exc:
                print(
                    f"[ours]   wv/{variant_id} FAILED "
                    f"({type(exc).__name__}: {exc}); skipping this variant"
                )
                wv_outcomes[variant_id] = {}

        outcomes.append(
            ProtocolOutcome(
                name=protocol.name,
                base_dir=base_dir,
                base_action_blocks_yaml=m1_yaml,
                base_lemma_verdicts=base_verdicts,
                action_variant_yamls=av_yamls,
                weakness_variant_outcomes=wv_outcomes,
            )
        )
        elapsed = time.monotonic() - protocol_started
        print(
            f"[ours] ({protocol_index}/{len(protocols)}) protocol "
            f"{protocol.name} done in {elapsed:.1f}s"
        )
        _events.log_event(
            stage="protocol",
            event_type="protocol_end",
            protocol=protocol.name,
            elapsed_seconds=elapsed,
            base_lemma_count=len(base_verdicts),
            action_variant_count=len(av_yamls),
            weakness_variant_count=len(wv_outcomes),
        )
    _events.log_event(
        stage="run_ours",
        event_type="batch_end",
        protocol_count=len(protocols),
    )
    return outcomes


def _process_base(
    *,
    protocol: ProtocolPaths,
    base_dir: Path,
    runner: Any,
    llm_caller: Callable[[str, str], str],
    extractor: Callable[..., Any],
    checkpoint: CheckpointManager,
    resume: bool,
    running_timeout_seconds_seconds: int | None = None,
) -> tuple[Path | None, dict[str, str]]:
    key = f"{protocol.name}:base"
    if resume and checkpoint.is_completed(key):
        cached = checkpoint.get_result(key) or {}
        m1 = cached.get("m1_yaml")
        print(f"[ours]   base SKIP (cached)")
        _events.log_event(
            stage="phase", event_type="phase_skip",
            protocol=protocol.name, phase="base",
        )
        return (
            Path(m1) if m1 else None,
            dict(cached.get("base_verdicts", {})),
        )
    started = time.monotonic()
    print(f"[ours]   base RUN")
    _events.log_event(
        stage="phase", event_type="phase_start",
        protocol=protocol.name, phase="base",
    )
    base_dir.mkdir(parents=True, exist_ok=True)

    def _compute() -> tuple[Path | None, dict[str, str]]:
        end_reason = MastaSystem(
            work_dir=base_dir,
            runner=runner,
            llm_caller=llm_caller,
        ).run(initial_document=protocol.document.read_text(encoding="utf-8"))
        _warn_if_unfinished("base", end_reason)
        m1 = extractor(work_dir=base_dir, output_dir=base_dir)
        verdicts = _verify_safety_lemmas(
            runner=runner, work_dir=base_dir, base_layout=True
        )
        return m1, verdicts

    status, payload = _run_compute_with_timeout(
        _compute, running_timeout_seconds_seconds
    )
    if status != "ok":
        print(
            f"[ours]   base {status.upper()}; treating as failed modeling, "
            "empty M1/M3 inputs"
        )
        _events.log_event(
            stage="phase", event_type="phase_timeout",
            protocol=protocol.name, phase="base", status=status,
        )
        return None, {}

    m1_yaml, base_verdicts = payload
    checkpoint.mark_completed(
        key,
        {
            "m1_yaml": str(m1_yaml) if m1_yaml is not None else None,
            "base_verdicts": dict(base_verdicts),
        },
    )
    elapsed = time.monotonic() - started
    print(
        f"[ours]   base DONE in {elapsed:.1f}s "
        f"(m1={'ok' if m1_yaml else 'miss'}, lemmas={len(base_verdicts)})"
    )
    _events.log_event(
        stage="phase", event_type="phase_end",
        protocol=protocol.name, phase="base",
        elapsed_seconds=elapsed,
        m1_extracted=m1_yaml is not None,
        lemma_count=len(base_verdicts),
    )
    return m1_yaml, base_verdicts


def _process_action_variant(
    *,
    protocol_name: str,
    variant_id: str,
    doc_path: Path,
    variant_dir: Path,
    runner: Any,
    llm_caller: Callable[[str, str], str],
    extractor: Callable[..., Any],
    checkpoint: CheckpointManager,
    resume: bool,
    running_timeout_seconds_seconds: int | None = None,
) -> Path | None:
    key = f"{protocol_name}:av:{variant_id}"
    if resume and checkpoint.is_completed(key):
        cached = checkpoint.get_result(key) or {}
        yaml_path = cached.get("yaml")
        print(f"[ours]   av/{variant_id} SKIP (cached)")
        _events.log_event(
            stage="phase", event_type="phase_skip",
            protocol=protocol_name, phase=f"av/{variant_id}",
        )
        return Path(yaml_path) if yaml_path else None
    started = time.monotonic()
    print(f"[ours]   av/{variant_id} RUN")
    _events.log_event(
        stage="phase", event_type="phase_start",
        protocol=protocol_name, phase=f"av/{variant_id}",
    )
    variant_dir.mkdir(parents=True, exist_ok=True)

    def _compute() -> Path | None:
        end_reason = MastaSystem(
            work_dir=variant_dir,
            runner=runner,
            llm_caller=llm_caller,
        ).run(initial_document=doc_path.read_text(encoding="utf-8"))
        _warn_if_unfinished(f"av/{variant_id}", end_reason)
        return extractor(work_dir=variant_dir, output_dir=variant_dir)

    status, payload = _run_compute_with_timeout(
        _compute, running_timeout_seconds_seconds
    )
    if status != "ok":
        print(
            f"[ours]   av/{variant_id} {status.upper()}; "
            "treating as failed modeling, empty M4 input"
        )
        _events.log_event(
            stage="phase", event_type="phase_timeout",
            protocol=protocol_name, phase=f"av/{variant_id}", status=status,
        )
        return None

    yaml_path = payload
    checkpoint.mark_completed(
        key,
        {"yaml": str(yaml_path) if yaml_path is not None else None},
    )
    elapsed = time.monotonic() - started
    print(
        f"[ours]   av/{variant_id} DONE in {elapsed:.1f}s "
        f"(m4_input={'ok' if yaml_path else 'miss'})"
    )
    _events.log_event(
        stage="phase", event_type="phase_end",
        protocol=protocol_name, phase=f"av/{variant_id}",
        elapsed_seconds=elapsed,
        m4_input_extracted=yaml_path is not None,
    )
    return yaml_path


def _process_weakness_variant(
    *,
    protocol_name: str,
    variant_id: str,
    doc_path: Path,
    variant_dir: Path,
    runner: Any,
    llm_caller: Callable[[str, str], str],
    checkpoint: CheckpointManager,
    resume: bool,
    running_timeout_seconds_seconds: int | None = None,
) -> dict[str, str]:
    key = f"{protocol_name}:wv:{variant_id}"
    if resume and checkpoint.is_completed(key):
        cached = checkpoint.get_result(key) or {}
        print(f"[ours]   wv/{variant_id} SKIP (cached)")
        _events.log_event(
            stage="phase", event_type="phase_skip",
            protocol=protocol_name, phase=f"wv/{variant_id}",
        )
        return dict(cached.get("verdicts", {}))
    started = time.monotonic()
    print(f"[ours]   wv/{variant_id} RUN")
    _events.log_event(
        stage="phase", event_type="phase_start",
        protocol=protocol_name, phase=f"wv/{variant_id}",
    )
    variant_dir.mkdir(parents=True, exist_ok=True)

    def _compute() -> dict[str, str]:
        end_reason = MastaSystem(
            work_dir=variant_dir,
            runner=runner,
            llm_caller=llm_caller,
        ).run(initial_document=doc_path.read_text(encoding="utf-8"))
        _warn_if_unfinished(f"wv/{variant_id}", end_reason)
        return _verify_safety_lemmas(
            runner=runner, work_dir=variant_dir, base_layout=False
        )

    status, payload = _run_compute_with_timeout(
        _compute, running_timeout_seconds_seconds
    )
    if status != "ok":
        print(
            f"[ours]   wv/{variant_id} {status.upper()}; "
            "treating as failed modeling, empty M5 verdicts"
        )
        _events.log_event(
            stage="phase", event_type="phase_timeout",
            protocol=protocol_name, phase=f"wv/{variant_id}", status=status,
        )
        return {}

    verdicts = payload
    checkpoint.mark_completed(key, {"verdicts": dict(verdicts)})
    elapsed = time.monotonic() - started
    print(
        f"[ours]   wv/{variant_id} DONE in {elapsed:.1f}s "
        f"(verdicts={len(verdicts)})"
    )
    _events.log_event(
        stage="phase", event_type="phase_end",
        protocol=protocol_name, phase=f"wv/{variant_id}",
        elapsed_seconds=elapsed,
        verdict_count=len(verdicts),
    )
    return verdicts


def _warn_if_unfinished(phase: str, end_reason: str) -> None:
    reason = (end_reason or "").strip().lower()
    if "workflow complete" in reason or reason.startswith("done"):
        return
    print(
        f"[ours]   WARNING phase {phase!r} ended with reason "
        f"{end_reason!r}; downstream artefacts may be incomplete"
    )


def _verify_safety_lemmas(
    *,
    runner: Any,
    work_dir: Path,
    base_layout: bool,
) -> dict[str, str]:
    """Re-run every safety lemma in ``model.spthy`` and persist outcomes.

    With ``base_layout=True`` per-lemma outcomes follow the dataset's
    ``lemmas/<NN>_<NAME>/{definition.txt,outcome.txt}`` convention via
    ``write_lemma_outcome`` (``lemma_type`` is empty because the agent
    emits no type metadata at runtime). With ``base_layout=False`` they
    follow the weakness-variant ``<LEMMA>/outcome.txt`` convention via
    ``write_weakness_lemma_outcome``. The full verdict map is also
    dumped to ``<work_dir>/lemma_verdicts.json`` for inspection.
    """

    spec_path = work_dir / "model.spthy"
    if not spec_path.exists():
        return {}
    spec_text = spec_path.read_text(encoding="utf-8")
    verdicts: dict[str, str] = {}
    for index, lemma_name in enumerate(
        extract_safety_lemma_names(spec_text), start=1
    ):
        result = runner.verify_lemma(spec_text, lemma_name)
        verdict = getattr(result, "verdict", None)
        if not isinstance(verdict, TamarinVerdict):
            verdict = TamarinVerdict.FALSIFIED
        verdicts[lemma_name] = verdict.value
        if base_layout:
            write_lemma_outcome(
                protocol_dir=work_dir,
                index=index,
                lemma_type="",
                lemma_name=lemma_name,
                definition_text=(
                    extract_lemma_definition(spec_text, lemma_name) or ""
                ),
                verdict=verdict,
            )
        else:
            write_weakness_lemma_outcome(
                weakness_variant_dir=work_dir,
                lemma_name=lemma_name,
                verdict=verdict,
            )
    (work_dir / "lemma_verdicts.json").write_text(
        json.dumps(verdicts, indent=2), encoding="utf-8"
    )
    return verdicts


def outcomes_to_predictions(
    outcomes: Sequence[ProtocolOutcome],
    specs: Sequence[ProtocolSpec],
) -> dict[str, ProtocolPrediction]:
    """Bridge between ``run_ours`` artefacts and ``score_protocol`` inputs.

    Reads, for every outcome whose protocol id appears in ``specs``:

    - the M1 YAML text from ``outcome.base_action_blocks_yaml``;
    - the per-lemma definition text from ``outcome.base_dir/model.spthy``
      via ``extract_safety_lemma_names`` + ``extract_lemma_definition``,
      paired positionally with the verdict map already collected in
      ``outcome.base_lemma_verdicts``;
    - the M4 YAML text from each ``outcome.action_variant_yamls[id]``;
    - the M5 verdict map from each
      ``outcome.weakness_variant_outcomes[id]``, normalising the string
      values back to ``TamarinVerdict``.

    Outcomes without a matching spec entry are skipped.
    """

    spec_by_id = {spec.id: spec for spec in specs}
    predictions: dict[str, ProtocolPrediction] = {}
    for outcome in outcomes:
        if outcome.name not in spec_by_id:
            continue
        base_blocks_text = _read_text_or_empty(outcome.base_action_blocks_yaml)
        base_lemma_definitions, base_lemma_verdicts = _extract_base_lemmas(
            outcome
        )
        action_variant_blocks_texts: dict[str, str] = {}
        for variant_id, yaml_path in outcome.action_variant_yamls.items():
            text = _read_text_or_empty(yaml_path)
            if text:
                action_variant_blocks_texts[variant_id] = text
        weakness_variant_verdicts: dict[
            str, dict[str, TamarinVerdict | None]
        ] = {}
        for variant_id, lemma_map in outcome.weakness_variant_outcomes.items():
            weakness_variant_verdicts[variant_id] = {
                name: _coerce_verdict(value) for name, value in lemma_map.items()
            }
        predictions[outcome.name] = ProtocolPrediction(
            protocol_id=outcome.name,
            base_action_blocks_text=base_blocks_text,
            base_lemma_definitions=base_lemma_definitions,
            base_lemma_verdicts=base_lemma_verdicts,
            action_variant_blocks_texts=action_variant_blocks_texts,
            weakness_variant_verdicts=weakness_variant_verdicts,
        )
    return predictions


def _extract_base_lemmas(
    outcome: ProtocolOutcome,
) -> tuple[dict[str, str], dict[str, TamarinVerdict | None]]:
    spthy_path = outcome.base_dir / "model.spthy"
    if not spthy_path.exists():
        return {}, {}
    spec_text = spthy_path.read_text(encoding="utf-8")
    names = extract_safety_lemma_names(spec_text)
    if not names:
        return {}, {}
    definitions = {
        name: (extract_lemma_definition(spec_text, name) or "")
        for name in names
    }
    verdicts = {
        name: _coerce_verdict(outcome.base_lemma_verdicts.get(name))
        for name in names
    }
    return definitions, verdicts


def _read_text_or_empty(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _coerce_verdict(value: object) -> TamarinVerdict | None:
    if isinstance(value, TamarinVerdict):
        return value
    if isinstance(value, str):
        try:
            return TamarinVerdict(value)
        except ValueError:
            return None
    return None
