# MASTA — Supplementary Material

This archive accompanies the MASTA submission. It bundles the system
source code, experiment runners, the renumbered benchmark dataset, the
raw run artefacts, and the figures referenced in the paper, sized so
that all results can be re-derived end-to-end without consulting any
external resource. API keys, account secrets, and absolute paths
particular to the authors' machine have been redacted.

## Directory layout

```
masta/
├── README.md                              # this file
├── requirements.txt                       # pip-installable third-party deps
├── tamarin_infra-0.1.9-py3-none-any.whl     # our authored Tamarin-Python bridge
├── src/                                   # MASTA system code
│   ├── agents/                            # agents and prompts
│   ├── evaluation/                        # scoring and evaluation
│   ├── llm/                               # LLM provider clients
│   ├── logging_/                          # event logging
│   ├── model/                             # shared data models
│   ├── tamarin/                           # Tamarin runner
│   ├── console.py                         # console utilities
│   └── orchestration.py                   # pipeline orchestration
├── scripts/                               # standalone entry points (generation, scoring, plotting)
├── experiments/                           # parallel runners that drive the four systems
├── data/
│   ├── dataset/                           # benchmark of 10 protocols (renumbered 001..010)
│   └── tamarin/                           # Tamarin reference documentation
├── runs/                                  # raw artefacts and score.json per run
└── figures/                               # paper-ready PDF/PNG plots and the long-format CSV
```

## Benchmark dataset (`data/dataset/`)

Ten protocol entries, numbered ``001..010``. Each entry
contains the natural-language ``document.txt``, the reference Tamarin
model ``model.spthy``, the action-block YAML used by M1, indexed
``lemmas/<NN>_<TYPE>_<NAME>/`` directories carrying the expected
verification outcome (``outcome.txt``) and — for verified lemmas —
the baseline proof-step count (``steps.txt``) used by M3. Action and
weakness variants live under ``action_variants/`` and
``weakness_variants/``.

Protocol identifiers and variant definitions are declared in
``data/dataset/metadata.yaml``.

## Run artefacts (`runs/`)

Seven experiment trees, one selected run per protocol; the backbone comparison
has three model labels and the other experiments have one each:

```
runs/
├── masta/qwen3.5-plus/<id>/run_1/        # full MASTA pipeline (paper system)
├── codex/gpt-5.5/<id>/run_1/             # codex CLI baseline
├── claude/opus-4.7/<id>/run_1/           # claude code CLI baseline
├── opencode/qwen3.5-plus/<id>/run_1/     # opencode CLI baseline
├── masta_no_arbiter/qwen3.5-plus/<id>/run_1/  # ablation: drop the Arbiter
├── masta_merged/qwen3.5-plus/<id>/run_1/      # ablation: merge Architect + Auditor
├── masta_backbone/<model>/<id>/run_1/      # three-model backbone comparison
├── backbone_results.json                  # aggregated backbone results
├── results.json                          # four-system summary produced by summarize_results.py
└── ablation_results.json                 # aggregated ablation results
```

Run directories contain available ``base/model.spthy`` and variant
``model.spthy`` files, action-block files, scoring logs, and ``score.json``
with M1-M5 evaluated against the specified dataset scope. Where present,
``run.log`` records the generation command. Working directories (``.work/``)
and the LLM cache used during generation are not distributed.

The ``masta`` system sweeps all 10 protocols. The two ablation systems
cover the first five protocols: ``001_cr_xor``, ``002_lak_06``,
``003_pattern_matching_and_destructors``, ``004_macro``, and
``005_tesla_scheme_1``.

The backbone comparison under ``runs/masta_backbone/`` evaluates
``qwen3.5-plus``, ``claude-sonnet-4-6``, and ``gpt-5-mini`` on protocols
001, 003, and 008 from ``data/dataset/``.

The single ``not_generated`` cell in the four-system grid
(``claude/opus-4.7/006_OPCUA_Secure_Channel/run_1``) corresponds to a
generation timeout; it is recorded with
``score.status: timeout`` and an all-zero ``score.json`` so the figures
include it as a real data point rather than dropping the row.

## Figures (`figures/`)

| File | Source |
| ---- | ------ |
| ``compare.{pdf,png}`` | grouped bar chart across the four main systems |
| ``heatmap.{pdf,png}`` | per-protocol M1-M5 grid, one panel per system |
| ``ablation.{pdf,png}`` | three-variant grouped bar restricted to the protocols the ablation systems actually scored |
| ``scores.csv`` | long-format dump of every (system, label, protocol, run) row aggregated by ``plot_results.py`` |

Each figure is regenerated from ``runs/<system>/<label>/<protocol>/run_*/score.json`` — there is no hidden state outside that tree.

## Installing dependencies

The pipeline assumes Python 3.12 and a working ``tamarin-prover`` binary.

```bash
# 1. Tamarin prover binary (1.10+ recommended) must be on $PATH.
#    Build / install per https://tamarin-prover.com — apt or `cabal install`.
tamarin-prover --version

# 2. A clean Python 3.12 virtual environment.
python3.12 -m venv .venv
source .venv/bin/activate

# 3. Third-party Python deps from PyPI.
pip install -r requirements.txt

# 4. Our authored Tamarin-Python bridge, shipped here as a wheel.
#    `tamarin_infra` provides TamarinClient / GoalSelector and is imported
#    by src/tamarin/runner.py; it is NOT on PyPI, so install it from the
#    bundled wheel directly.
pip install ./tamarin_infra-0.1.9-py3-none-any.whl
```

After step 4, ``python -c "import tamarin_infra"`` should succeed and
``which tamarin-prover`` should resolve. No other system packages are
required.

## Reproducing the scores

The scoring pipeline calls ``tamarin-prover`` directly and is fully
deterministic. To replay it inside this archive:

```bash
# Re-score every run against the renumbered dataset.
python -m experiments.run_score --level all
python -m scripts.score_ablation --systems masta_no_arbiter,masta_merged

# Re-aggregate.
python -m scripts.summarize_results --output runs/results.json
python -m scripts.summarize_ablation \
    --systems masta,masta_no_arbiter,masta_merged \
    --protocols 001_cr_xor,002_lak_06,003_pattern_matching_and_destructors,004_macro,005_tesla_scheme_1 \
    --output runs/ablation_results.json

# Re-plot.
python -m scripts.plot_results --runs-dir runs --output-dir figures
```

For backbone scoring and aggregation:

```bash
python -m scripts.score_backbone --force
python -m scripts.summarize_ablation \
    --systems masta_backbone \
    --output runs/backbone_results.json
```

All four steps consult only ``data/dataset/`` and ``runs/<system>/<label>/<protocol>/run_*/{base,action_variants,weakness_variants}/model.spthy``; no network access or LLM credentials are needed.

The pre-computed baseline proof-step counts (``data/dataset/<id>/lemmas/<NN>_<TYPE>_<NAME>/steps.txt``) feed M3's trace-length term. These reference counts are included in the dataset.
