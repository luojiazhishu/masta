# experiments/

Two phases, six runners. Generation runners — `run_masta.py`,
`run_oneshot.py`, and `run_coding_agent.py --system codex|claude|opencode`
— produce `model.spthy` per (protocol|variant, run_idx). The scoring
runner — `run_score.py` — walks the runs tree and writes per-base
`score.json`. The aggregate dispatcher `run_all.py` orchestrates one
or both phases. Every runner reads the same unified YAML at
`experiments/experiment.yaml`.

## Configuration

Copy the template and fill in the providers section:

```bash
cp experiments/experiment.example.yaml experiments/experiment.yaml
$EDITOR experiments/experiment.yaml
```

The active config is git-ignored (it carries API keys); only the
`.example.yaml` template is tracked. The fields you typically edit are:

- `dataset_dir` / `runs_dir`
- `runs_per_protocol` (each protocol is generated this many times, with
  `seed = llm_seed + run_idx - 1`)
- `parallel` (worker process pool size)
- `systems.<name>.models` (per-system model list — see the two forms
  below)
- `providers.<provider_id>.{provider,model,api_key,base_url}`
- `experiments` (default subset that `run_all.py` will execute)

Each `systems.<name>.models` entry is one of:

- `<provider_id>` — a key from the `providers` mapping. The runner
  pushes that provider's credentials and real model name into the
  system; artifacts land under `runs/<system>/<provider_id>/...`.
- `default(<tag>)` — coding-agent only (`codex` / `claude` /
  `opencode`). The host CLI uses its globally-configured account and
  default model; `<tag>` (allowed chars: `A-Z a-z 0-9 . _ -`) only
  labels the artifact subdir as `runs/<system>/default_<tag>/...`.

`masta` and `oneshot` drive an LLM provider directly, so they MUST use
the `<provider_id>` form; `default(...)` is rejected for them. The
coding-agent systems accept either form: `default(...)` to delegate to
the host CLI's global account, or a `<provider_id>` to push a specific
provider's credentials and real model into the CLI's per-workspace
config (`.codex/config.toml` / `opencode.json` / `.claude/settings.json`).

Each `providers.<provider_id>` entry exposes a real model name via the
required `model` field — the YAML key is the local id used by `systems`
and the artifact tree, while `model` is the literal string sent to the
API.

## Per-system generators

```bash
.venv/bin/python -m experiments.run_masta
.venv/bin/python -m experiments.run_oneshot
.venv/bin/python -m experiments.run_coding_agent --system codex
.venv/bin/python -m experiments.run_coding_agent --system claude
.venv/bin/python -m experiments.run_coding_agent --system opencode
```

Each generator:

- enumerates jobs from `dataset_dir` (one job per
  `(protocol|action_variant|weakness_variant, run_idx)` triple);
- skips jobs whose `model.spthy` is already on disk (chunked
  resumability, base and variants treated identically);
- skips jobs whose previous attempt timed out or exited non-zero
  (recorded in `<output_dir>/gen.status`); pass `--retry-failed` to
  re-attempt those;
- spawns a `ProcessPoolExecutor` capped at `parallel`;
- per worker: invokes the matching `scripts/<system>_model.py`, which
  runs the generation under `<output_dir>/.work/` and copies the
  produced `model.spthy` up to `<output_dir>/`;
- writes `run.log` into each output directory (`.work/` keeps every
  intermediate file the generator produced) and `gen.status` whenever
  the attempt did not produce a valid `model.spthy`.

Output layout:

```
<runs_dir>/<system>/<safe_label>/<protocol>/run_<i>/
    base/
        model.spthy
        score.json    ← written by run_score (later phase)
        score.log
        run.log
        .work/        ← agents' work tree (document copy, skills/,
                        per-tool config, .agent/stdio, llm_cache, ...)
    action_variants/
        01/
            model.spthy
            run.log
            .work/
        02/...
    weakness_variants/
        01/
            model.spthy
            run.log
            .work/
        02/...
```

`<safe_label>` is the YAML key from `providers` (provider-bound entries)
or `default_<tag>` (`default(...)` entries). `.work/` is preserved
indefinitely — it is your debug surface for failed jobs.

## Scoring runner: `run_score.py`

```bash
.venv/bin/python -m experiments.run_score                          # both levels (default)
.venv/bin/python -m experiments.run_score --level base             # per-base M1/M2/M3
.venv/bin/python -m experiments.run_score --level protocol         # per-run_<i> M1-M5
.venv/bin/python -m experiments.run_score --system masta,oneshot
```

`--level` is one of `base` / `protocol` / `all` (default `all`: base
then protocol).

**Base level** — for every system × label combination walks
`<runs_dir>/<system>/<safe_label>/<protocol>/run_<i>/base/model.spthy`,
invokes `scripts.score_model`, writes `base/score.json` +
`base/score.log`. Captures M1 / M2 / M3 (M4 / M5 are placeholder
values at this level — no variant inputs supplied). Skip rules:

- `score.json` already on disk → done, skipped.
- `model.spthy` missing → reported as `no model`, not attempted.
- previous attempt's `base/score.status` recorded a failure → skipped;
  pass `--retry-failed` to re-attempt.

**Protocol level** — for every `run_<i>/` whose `base/model.spthy` and
every expected variant `model.spthy` (per dataset spec) are present,
invokes `scripts.score_protocol`, writes `run_<i>/score.json` +
`run_<i>/score.log` with M1-M5 fully populated. Skip rules:

- `run_<i>/score.json` already on disk → done, skipped.
- any expected base / variant `model.spthy` missing → reported as
  `incomplete`, not attempted.
- previous attempt's `run_<i>/score.status` recorded a failure →
  skipped; pass `--retry-failed` to re-attempt.

## Aggregate runner: `run_all.py`

```bash
.venv/bin/python -m experiments.run_all                                            # cfg.experiments, phase=all
.venv/bin/python -m experiments.run_all --phase generate
.venv/bin/python -m experiments.run_all --phase score --experiments masta,oneshot
.venv/bin/python -m experiments.run_all --experiments codex,claude --parallel 4
```

`--phase` is one of `generate` / `score` / `all` (default `all`:
generate then score). `--experiments` limits the systems under
consideration in either phase; defaults to `cfg.experiments` from the
YAML. `--retry-failed` is forwarded to every sub-runner so a job whose
prior attempt timed out or exited non-zero is re-attempted instead of
skipped. A failing sub-runner is reported in the final summary but does
not stop the next one.

## After the run

Each base run drops its own `score.json`; aggregate them with:

```bash
find <runs_dir>/<system>/<safe_label> -name score.json -path '*/run_*/base/*'
```

For dataset-level means across runs, write your own aggregator on top of
the per-base JSON.
