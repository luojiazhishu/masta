"""Shared bits for experiments.run_{masta,oneshot,coding_agent} and run_all.

Every experiment runner reads the same ``experiments/experiment.yaml``,
applies the active model's provider env (when applicable), enumerates
per-protocol jobs, and (after each successful generation) immediately
spawns ``scripts.score_model`` to drop a per-protocol ``score.json``.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml


_DEFAULT_TAG_RE = re.compile(r"^default\(([A-Za-z0-9._-]+)\)$")

GEN_STATUS_FILENAME = "gen.status"
SCORE_STATUS_FILENAME = "score.status"

Phase = Literal["generate", "score"]
_STATUS_FILENAMES: dict[Phase, str] = {
    "generate": GEN_STATUS_FILENAME,
    "score": SCORE_STATUS_FILENAME,
}


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "experiments" / "experiment.yaml"
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "experiments" / "experiment.example.yaml"

CODING_AGENT_SYSTEMS: tuple[str, ...] = ("codex", "claude", "opencode")
DIRECT_API_SYSTEMS: tuple[str, ...] = ("masta", "oneshot")
KNOWN_EXPERIMENTS: tuple[str, ...] = DIRECT_API_SYSTEMS + CODING_AGENT_SYSTEMS
KNOWN_PROVIDERS: frozenset[str] = frozenset({"openai", "anthropic"})

CODING_AGENT_TOOL: dict[str, str] = {
    "codex": "codex",
    "claude": "claude_code",
    "opencode": "opencode",
}


class ExperimentConfigError(ValueError):
    """Raised when ``experiment.yaml`` is missing or fails validation."""


@dataclass(frozen=True)
class ProviderEntry:
    provider: str
    model: str
    api_key: str
    base_url: str = ""


@dataclass(frozen=True)
class ModelEntry:
    """One element of ``systems.<name>.models`` after parsing.

    ``label`` is the safe string that names the artifact subdir under
    ``runs/<system>/<label>/``.

    ``provider_id`` is the YAML key under ``providers`` when this entry
    is bound to a registered provider; ``None`` means the coding-agent
    CLI should use its own globally-configured account (``default(...)``
    form, only legal for ``codex`` / ``claude`` / ``opencode``).

    ``default_tag`` carries the user-supplied annotation inside
    ``default(...)`` so the runner can echo a meaningful model id; it is
    ``None`` when the entry is provider-bound.
    """

    label: str
    provider_id: str | None
    default_tag: str | None


@dataclass(frozen=True)
class SystemConfig:
    name: str
    models: tuple[ModelEntry, ...]


@dataclass(frozen=True)
class ExperimentConfig:
    dataset_dir: Path
    runs_dir: Path
    runs_per_protocol: int
    parallel: int
    tamarin_timeout_seconds: int
    task_timeout_seconds: int
    experiments: tuple[str, ...]
    systems: dict[str, SystemConfig]
    providers: dict[str, ProviderEntry]
    llm_temperature: float
    llm_seed: int
    llm_max_tokens: int | None
    llm_reasoning: bool
    llm_reasoning_budget: int | None

    def system_for(self, name: str) -> SystemConfig:
        try:
            return self.systems[name]
        except KeyError as exc:
            raise ExperimentConfigError(
                f"system {name!r} is not declared under 'systems'"
            ) from exc

    def provider_for(self, provider_id: str) -> ProviderEntry:
        try:
            return self.providers[provider_id]
        except KeyError as exc:
            raise ExperimentConfigError(
                f"provider {provider_id!r} is not declared under 'providers'"
            ) from exc


def load_experiment_config(path: Path | None = None) -> ExperimentConfig:
    """Read and validate the unified experiment config.

    The path defaults to ``experiments/experiment.yaml``. If it does not
    exist a clear error directs the caller to copy the example template.
    """

    cfg_path = path if path is not None else DEFAULT_CONFIG_PATH
    if not cfg_path.is_file():
        raise ExperimentConfigError(
            f"experiment config not found: {cfg_path}\n"
            f"copy {EXAMPLE_CONFIG_PATH.name} to experiment.yaml under "
            f"experiments/ and fill in the providers section."
        )
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ExperimentConfigError(f"invalid YAML in {cfg_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ExperimentConfigError(
            f"top-level YAML must be a mapping, got {type(raw).__name__}"
        )

    dataset_dir = _require_path(raw, "dataset_dir")
    runs_dir = _require_path(raw, "runs_dir")
    runs_per_protocol = _require_positive_int(raw, "runs_per_protocol")
    parallel = _require_positive_int(raw, "parallel")
    tamarin_timeout = _require_positive_int(raw, "tamarin_timeout_seconds")
    task_timeout = _require_positive_int(raw, "task_timeout_seconds")
    experiments = _parse_experiment_list(raw.get("experiments"))
    providers_raw = _require_mapping(raw, "providers")
    providers = {
        name: _parse_provider_entry(name, entry)
        for name, entry in providers_raw.items()
    }
    systems = _parse_systems(raw.get("systems"), providers=providers)

    for system_name in experiments:
        if system_name not in systems:
            raise ExperimentConfigError(
                f"experiment {system_name!r} is enabled but missing from 'systems'"
            )

    llm_temperature = _coerce_float(raw, "llm_temperature", default=0.0)
    llm_seed = _coerce_int(raw, "llm_seed", default=42)
    llm_max_tokens = _coerce_optional_int(raw, "llm_max_tokens")
    llm_reasoning = bool(raw.get("llm_reasoning", False))
    llm_reasoning_budget = _coerce_optional_int(raw, "llm_reasoning_budget")

    return ExperimentConfig(
        dataset_dir=dataset_dir,
        runs_dir=runs_dir,
        runs_per_protocol=runs_per_protocol,
        parallel=parallel,
        tamarin_timeout_seconds=tamarin_timeout,
        task_timeout_seconds=task_timeout,
        experiments=experiments,
        systems=systems,
        providers=providers,
        llm_temperature=llm_temperature,
        llm_seed=llm_seed,
        llm_max_tokens=llm_max_tokens,
        llm_reasoning=llm_reasoning,
        llm_reasoning_budget=llm_reasoning_budget,
    )


def apply_provider_env(cfg: ExperimentConfig, provider: ProviderEntry) -> dict[str, str]:
    """Export ``MASTA_LLM_*`` env vars for the given provider entry.

    Only meaningful for systems that drive the provider directly
    (``masta`` / ``oneshot``); coding-agent ``default(...)`` entries
    skip this helper and let the host CLI use its global account.
    """

    env = {
        "MASTA_LLM_PROVIDER": provider.provider,
        "MASTA_LLM_MODEL": provider.model,
    }
    for key in ("MASTA_LLM_BASE_URL", "MASTA_LLM_API_KEY"):
        os.environ.pop(key, None)
    if provider.base_url:
        env["MASTA_LLM_BASE_URL"] = provider.base_url
    if provider.api_key:
        env["MASTA_LLM_API_KEY"] = provider.api_key
    for key in ("MASTA_LLM_REASONING", "MASTA_LLM_REASONING_BUDGET"):
        os.environ.pop(key, None)
    if cfg.llm_reasoning:
        env["MASTA_LLM_REASONING"] = "1"
    if cfg.llm_reasoning_budget is not None:
        env["MASTA_LLM_REASONING_BUDGET"] = str(cfg.llm_reasoning_budget)
    for k, v in env.items():
        os.environ[k] = v
    return env


def safe_model_name(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", label)


def output_root_for(cfg: ExperimentConfig, *, system: str, label: str) -> Path:
    """Resolve the per-(system, label) artifact root under ``runs_dir``."""

    return cfg.runs_dir / system / safe_model_name(label)


@dataclass(frozen=True)
class ProtocolJob:
    """One (protocol|variant, run_idx) generation+score job."""

    protocol_id: str          # e.g. "002_cr_xor" (always the dataset entry id)
    document: Path            # path to document.txt
    output_dir: Path          # where model.spthy + score.json will land
    label: str                # human-readable, includes variant tag and run idx
    seed: int                 # llm_seed offset by run_idx
    is_base: bool             # True iff this is the protocol's primary document


def enumerate_jobs(
    cfg: ExperimentConfig,
    *,
    output_root: Path,
    include_variants: bool = True,
) -> list[ProtocolJob]:
    """Build the (protocol|variant) × runs flat job list.

    Only protocols registered in ``metadata.yaml`` are enumerated; any
    sibling directory under ``dataset_dir`` (e.g. ``X_*`` exclusions) is
    silently ignored.

    Each base job lands at ``output_root/<protocol>/run_<i>/base/``;
    each variant job lands at
    ``output_root/<protocol>/run_<i>/<kind>/<NN>/``. ``include_variants``
    is True when the caller wants both base and variant jobs.
    """

    from src.evaluation.dataset_loader import load_dataset

    jobs: list[ProtocolJob] = []
    for spec in sorted(load_dataset(cfg.dataset_dir), key=lambda s: s.id):
        protocol_id = spec.id
        entry_dir = cfg.dataset_dir / protocol_id
        primary = entry_dir / "document.txt"
        if primary.is_file():
            for run_idx in range(1, cfg.runs_per_protocol + 1):
                run_root = output_root / protocol_id / f"run_{run_idx}"
                jobs.append(
                    ProtocolJob(
                        protocol_id=protocol_id,
                        document=primary,
                        output_dir=run_root / "base",
                        label=f"{protocol_id} run_{run_idx} base",
                        seed=cfg.llm_seed + run_idx - 1,
                        is_base=True,
                    )
                )
        if not include_variants:
            continue
        for kind in ("action_variants", "weakness_variants"):
            kind_dir = entry_dir / kind
            if not kind_dir.is_dir():
                continue
            for variant in sorted(p for p in kind_dir.iterdir() if p.is_dir()):
                doc = variant / "document.txt"
                if not doc.is_file():
                    continue
                for run_idx in range(1, cfg.runs_per_protocol + 1):
                    run_root = output_root / protocol_id / f"run_{run_idx}"
                    jobs.append(
                        ProtocolJob(
                            protocol_id=protocol_id,
                            document=doc,
                            output_dir=run_root / kind / variant.name,
                            label=(
                                f"{protocol_id} run_{run_idx} "
                                f"{kind}/{variant.name}"
                            ),
                            seed=cfg.llm_seed + run_idx - 1,
                            is_base=False,
                        )
                    )
    return jobs


def is_generated(job: ProtocolJob) -> bool:
    """True iff the generation phase has produced a non-empty model.spthy."""

    model_path = job.output_dir / "model.spthy"
    if not model_path.is_file():
        return False
    try:
        return model_path.stat().st_size > 0
    except OSError:
        return False


def is_scored(job: ProtocolJob) -> bool:
    """True iff the per-base score has been computed (variants always False)."""

    if not job.is_base:
        return False
    return (job.output_dir / "score.json").is_file()


@dataclass(frozen=True)
class ProtocolRunJob:
    """One protocol-level scoring job: the entire ``run_<i>/`` subtree."""

    protocol_id: str
    run_dir: Path           # the run_<i>/ root directory
    label: str              # human-readable, e.g. "002_cr_xor run_1"


def enumerate_protocol_runs(
    cfg: ExperimentConfig, *, output_root: Path
) -> list[ProtocolRunJob]:
    """Build the (protocol, run_idx) flat list rooted at ``output_root``.

    Only protocols registered in ``metadata.yaml`` are enumerated; sibling
    directories like ``X_*`` are silently ignored to mirror
    :func:`enumerate_jobs`.
    """

    from src.evaluation.dataset_loader import load_dataset

    jobs: list[ProtocolRunJob] = []
    for spec in sorted(load_dataset(cfg.dataset_dir), key=lambda s: s.id):
        protocol_id = spec.id
        for run_idx in range(1, cfg.runs_per_protocol + 1):
            run_dir = output_root / protocol_id / f"run_{run_idx}"
            jobs.append(
                ProtocolRunJob(
                    protocol_id=protocol_id,
                    run_dir=run_dir,
                    label=f"{protocol_id} run_{run_idx}",
                )
            )
    return jobs


def is_protocol_scored(run_job: ProtocolRunJob) -> bool:
    """True iff the protocol-level score.json has been written."""

    return (run_job.run_dir / "score.json").is_file()


@dataclass(frozen=True)
class ProtocolRunSelection:
    pending: list[ProtocolRunJob]
    done: int
    incomplete: int                       # base + variant model.spthy not all present
    prior_failures: int
    prior_failure_outcomes: dict[str, int]


def select_protocol_score_jobs(
    run_jobs: list[ProtocolRunJob],
    *,
    expected_variant_dirs=None,           # unused; kept for call-site compat
    retry_failed: bool,
) -> ProtocolRunSelection:
    """Partition protocol-level scoring jobs.

    A run is ``done`` when ``run_dir/score.json`` is on disk.
    A run is ``incomplete`` only when ``run_dir`` itself does not exist
    (the generate phase never started). In all other cases (base or
    variant model.spthy absent) ``score_protocol`` will write zero scores
    for missing pieces, so the run is always ``pending`` and scored.
    A prior failure recorded in ``run_dir/score.status`` is respected
    unless ``retry_failed`` is true.
    """

    pending: list[ProtocolRunJob] = []
    done = 0
    incomplete = 0
    prior_failures = 0
    failure_outcomes: dict[str, int] = {}
    for rj in run_jobs:
        if is_protocol_scored(rj):
            done += 1
            continue
        if not rj.run_dir.is_dir():
            incomplete += 1
            continue
        prior = read_status(rj.run_dir, "score")
        if prior is not None and prior != "ok" and not retry_failed:
            prior_failures += 1
            failure_outcomes[prior] = failure_outcomes.get(prior, 0) + 1
            continue
        pending.append(rj)
    return ProtocolRunSelection(
        pending=pending,
        done=done,
        incomplete=incomplete,
        prior_failures=prior_failures,
        prior_failure_outcomes=failure_outcomes,
    )


def _nonempty_file(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def status_path(output_dir: Path, phase: Phase) -> Path:
    return output_dir / _STATUS_FILENAMES[phase]


def read_status(output_dir: Path, phase: Phase) -> str | None:
    """Return the recorded ``outcome`` of the last attempt, or ``None``."""

    path = status_path(output_dir, phase)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    outcome = data.get("outcome")
    return outcome if isinstance(outcome, str) else None


def write_status(output_dir: Path, phase: Phase, outcome: str) -> None:
    """Persist the outcome of the just-finished attempt."""

    path = status_path(output_dir, phase)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "phase": phase,
        "outcome": outcome,
        "ended_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


@dataclass(frozen=True)
class JobSelection:
    """Result of partitioning jobs into done/skip/pending buckets."""

    pending: list[ProtocolJob]
    done: int                            # already produced the artifact
    prior_failures: int                  # last attempt's status != ok, no --retry
    prior_failure_outcomes: dict[str, int]  # outcome -> count


def select_generate_jobs(
    jobs: list[ProtocolJob], *, retry_failed: bool
) -> JobSelection:
    """Partition generate jobs into done / prior-failure / pending."""

    pending: list[ProtocolJob] = []
    done = 0
    prior_failures = 0
    failure_outcomes: dict[str, int] = {}
    for job in jobs:
        if is_generated(job):
            done += 1
            continue
        prior = read_status(job.output_dir, "generate")
        if prior is not None and prior != "ok" and not retry_failed:
            prior_failures += 1
            failure_outcomes[prior] = failure_outcomes.get(prior, 0) + 1
            continue
        pending.append(job)
    return JobSelection(
        pending=pending,
        done=done,
        prior_failures=prior_failures,
        prior_failure_outcomes=failure_outcomes,
    )


def select_score_jobs(
    jobs: list[ProtocolJob], *, retry_failed: bool
) -> tuple[JobSelection, int]:
    """Partition base-score jobs; also returns count of jobs missing model."""

    pending: list[ProtocolJob] = []
    done = 0
    prior_failures = 0
    missing_model = 0
    failure_outcomes: dict[str, int] = {}
    for job in jobs:
        if is_scored(job):
            done += 1
            continue
        if not is_generated(job):
            missing_model += 1
            continue
        prior = read_status(job.output_dir, "score")
        if prior is not None and prior != "ok" and not retry_failed:
            prior_failures += 1
            failure_outcomes[prior] = failure_outcomes.get(prior, 0) + 1
            continue
        pending.append(job)
    return (
        JobSelection(
            pending=pending,
            done=done,
            prior_failures=prior_failures,
            prior_failure_outcomes=failure_outcomes,
        ),
        missing_model,
    )


def run_protocol_score(
    *,
    run_job: ProtocolRunJob,
    dataset_dir: Path,
    tamarin_timeout: int,
    task_timeout: int,
    force: bool = False,
) -> int | str | None:
    """Run ``scripts.score_protocol`` against one ``run_<i>/`` tree.

    Returns the subprocess exit code on a normal exit, the literal
    string ``"timeout"`` on task_timeout, or ``None`` when the base
    model.spthy is missing. Stdio is tee'd to ``run_<i>/score.log``.
    """

    base_model = run_job.run_dir / "base" / "model.spthy"
    if not base_model.is_file():
        return None
    log_path = run_job.run_dir / "score.log"
    cmd = [
        sys.executable,
        "-m",
        "scripts.score_protocol",
        "--run-dir",
        str(run_job.run_dir),
        "--dataset-dir",
        str(dataset_dir),
        "--protocol-id",
        run_job.protocol_id,
        "--tamarin-timeout",
        str(tamarin_timeout),
    ]
    if force:
        cmd.append("--force")
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"$ {' '.join(cmd)}\n\n")
        log.flush()
        try:
            proc = subprocess.run(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=task_timeout,
            )
        except subprocess.TimeoutExpired:
            log.write(f"\n[score timeout after {task_timeout}s]\n")
            return "timeout"
    return proc.returncode


def run_base_score(
    *,
    job: ProtocolJob,
    dataset_dir: Path,
    tamarin_timeout: int,
    task_timeout: int,
) -> int | str | None:
    """Run ``scripts.score_model`` against a base job's model.spthy.

    Returns the subprocess exit code on a normal exit, the literal
    string ``"timeout"`` if ``task_timeout`` was hit, or ``None`` when
    the model.spthy is missing.  Standard out / err are tee'd to
    ``score.log`` next to ``score.json``.
    """

    model_path = job.output_dir / "model.spthy"
    if not model_path.is_file():
        return None
    log_path = job.output_dir / "score.log"
    cmd = [
        sys.executable,
        "-m",
        "scripts.score_model",
        "--model",
        str(model_path),
        "--dataset-dir",
        str(dataset_dir),
        "--protocol-id",
        job.protocol_id,
        "--tamarin-timeout",
        str(tamarin_timeout),
    ]
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"$ {' '.join(cmd)}\n\n")
        log.flush()
        try:
            proc = subprocess.run(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=task_timeout,
            )
        except subprocess.TimeoutExpired:
            log.write(f"\n[score timeout after {task_timeout}s]\n")
            return "timeout"
    return proc.returncode


def _require_str(raw: dict, key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ExperimentConfigError(
            f"'{key}' must be a non-empty string, got {value!r}"
        )
    return value.strip()


def _require_path(raw: dict, key: str) -> Path:
    return Path(_require_str(raw, key)).expanduser()


def _require_positive_int(raw: dict, key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or value <= 0:
        raise ExperimentConfigError(
            f"'{key}' must be a positive integer, got {value!r}"
        )
    return value


def _require_mapping(raw: dict, key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict) or not value:
        raise ExperimentConfigError(
            f"'{key}' must be a non-empty mapping"
        )
    return value


def _coerce_float(raw: dict, key: str, *, default: float) -> float:
    value = raw.get(key, default)
    if not isinstance(value, (int, float)):
        raise ExperimentConfigError(f"'{key}' must be a number, got {value!r}")
    return float(value)


def _coerce_int(raw: dict, key: str, *, default: int) -> int:
    value = raw.get(key, default)
    if not isinstance(value, int):
        raise ExperimentConfigError(f"'{key}' must be an integer, got {value!r}")
    return value


def _coerce_optional_int(raw: dict, key: str) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, int):
        raise ExperimentConfigError(f"'{key}' must be an integer or null, got {value!r}")
    return value


def _parse_model_list(
    owner: str,
    raw: object,
    *,
    providers: dict[str, ProviderEntry],
    allow_default: bool,
) -> tuple[ModelEntry, ...]:
    if not isinstance(raw, list) or not raw:
        raise ExperimentConfigError(
            f"'systems.{owner}.models' must be a non-empty list"
        )
    out: list[ModelEntry] = []
    seen_labels: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ExperimentConfigError(
                f"'systems.{owner}.models' entries must be non-empty strings, "
                f"got {item!r}"
            )
        item = item.strip()
        match = _DEFAULT_TAG_RE.match(item)
        if match:
            if not allow_default:
                raise ExperimentConfigError(
                    f"'systems.{owner}.models' contains {item!r}; the "
                    f"'default(...)' form is only allowed for coding-agent "
                    f"systems ({list(CODING_AGENT_SYSTEMS)})"
                )
            tag = match.group(1)
            entry = ModelEntry(
                label=f"{tag}",
                provider_id=None,
                default_tag=tag,
            )
        elif item.startswith("default(") or item.endswith(")"):
            raise ExperimentConfigError(
                f"'systems.{owner}.models' entry {item!r} looks like a "
                f"malformed default(...) form; the tag inside the parens "
                f"may only contain [A-Za-z0-9._-]"
            )
        else:
            if item not in providers:
                raise ExperimentConfigError(
                    f"'systems.{owner}.models' references {item!r} which is "
                    f"not a registered provider id and not a default(...) form"
                )
            entry = ModelEntry(label=item, provider_id=item, default_tag=None)
        if entry.label in seen_labels:
            continue
        seen_labels.add(entry.label)
        out.append(entry)
    return tuple(out)


def _parse_systems(
    raw: object, *, providers: dict[str, ProviderEntry]
) -> dict[str, SystemConfig]:
    if not isinstance(raw, dict) or not raw:
        raise ExperimentConfigError(
            "'systems' must be a non-empty mapping of <system> -> {models: [...]}"
        )
    out: dict[str, SystemConfig] = {}
    for name, body in raw.items():
        if name not in KNOWN_EXPERIMENTS:
            raise ExperimentConfigError(
                f"systems[{name!r}] is not one of {list(KNOWN_EXPERIMENTS)}"
            )
        if not isinstance(body, dict):
            raise ExperimentConfigError(
                f"systems[{name!r}] must be a mapping, got {type(body).__name__}"
            )
        allow_default = name in CODING_AGENT_SYSTEMS
        models = _parse_model_list(
            name, body.get("models"), providers=providers, allow_default=allow_default
        )
        out[name] = SystemConfig(name=name, models=models)
    return out


def _parse_experiment_list(raw: object) -> tuple[str, ...]:
    if raw is None:
        return KNOWN_EXPERIMENTS
    if not isinstance(raw, list) or not raw:
        raise ExperimentConfigError(
            "'experiments' must be a non-empty list of system names"
        )
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str) or item not in KNOWN_EXPERIMENTS:
            raise ExperimentConfigError(
                f"experiment {item!r} not in {KNOWN_EXPERIMENTS}"
            )
        if item not in out:
            out.append(item)
    return tuple(out)


def _parse_provider_entry(provider_id: str, raw: object) -> ProviderEntry:
    if not isinstance(raw, dict):
        raise ExperimentConfigError(
            f"providers[{provider_id!r}] must be a mapping, got {type(raw).__name__}"
        )
    provider = raw.get("provider")
    if not isinstance(provider, str) or provider not in KNOWN_PROVIDERS:
        raise ExperimentConfigError(
            f"providers[{provider_id!r}].provider must be one of "
            f"{sorted(KNOWN_PROVIDERS)}, got {provider!r}"
        )
    model = raw.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ExperimentConfigError(
            f"providers[{provider_id!r}].model must be a non-empty string "
            f"(the real model name pushed to the API)"
        )
    api_key = raw.get("api_key")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ExperimentConfigError(
            f"providers[{provider_id!r}].api_key must be a non-empty string"
        )
    base_url = raw.get("base_url", "")
    if base_url is None:
        base_url = ""
    if not isinstance(base_url, str):
        raise ExperimentConfigError(
            f"providers[{provider_id!r}].base_url must be a string"
        )
    return ProviderEntry(
        provider=provider,
        model=model.strip(),
        api_key=api_key.strip(),
        base_url=base_url.strip(),
    )


__all__ = [
    "CODING_AGENT_SYSTEMS",
    "CODING_AGENT_TOOL",
    "DEFAULT_CONFIG_PATH",
    "DIRECT_API_SYSTEMS",
    "EXAMPLE_CONFIG_PATH",
    "KNOWN_EXPERIMENTS",
    "PROJECT_ROOT",
    "ExperimentConfig",
    "ExperimentConfigError",
    "GEN_STATUS_FILENAME",
    "JobSelection",
    "SCORE_STATUS_FILENAME",
    "ModelEntry",
    "Phase",
    "ProtocolJob",
    "ProtocolRunJob",
    "ProtocolRunSelection",
    "ProviderEntry",
    "SystemConfig",
    "apply_provider_env",
    "enumerate_jobs",
    "enumerate_protocol_runs",
    "is_generated",
    "is_protocol_scored",
    "is_scored",
    "load_experiment_config",
    "output_root_for",
    "read_status",
    "run_base_score",
    "run_protocol_score",
    "safe_model_name",
    "select_generate_jobs",
    "select_protocol_score_jobs",
    "select_score_jobs",
    "status_path",
    "write_status",
]
