"""Aggregate score.json files under runs/ and produce paper figures.

Outputs (default into ./figures/):
- compare.pdf / compare.png   : 4-system grouped bar over M1..M5 means
- ablation.pdf / ablation.png : 3-variant grouped bar over M1..M5 means
- heatmap.pdf / heatmap.png   : per-protocol M1..M5 heatmap, 4 systems
- scores.csv                  : raw long-format dump for supplementary use
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap


METRICS: tuple[str, ...] = ("m1", "m2", "m3", "m4", "m5")
METRIC_LABELS: tuple[str, ...] = ("M1", "M2", "M3", "M4", "M5")

# Paper palette (blue / violet / pink, looks good in print + on screen).
COLOR_DEEP_BLUE = "#33539E"     # 深靛蓝 — MASTA / hero column
COLOR_SOFT_BLUE = "#7FACD6"     # 天空蓝
COLOR_LIGHT_PURPLE = "#BFB8DA"  # 淡丁香紫
COLOR_BLOSSOM_PINK = "#E9B7D4"  # 樱花粉
COLOR_GREY_PURPLE = "#A5678E"   # 莫兰迪紫

# Continuous heatmap colormap (light → deep, single hue family).
HEATMAP_CMAP = LinearSegmentedColormap.from_list(
    "paper_blues", [COLOR_LIGHT_PURPLE, COLOR_SOFT_BLUE, COLOR_DEEP_BLUE]
)


# --- comparison vs ablation system selectors ----------------------------

@dataclass(frozen=True)
class SystemSpec:
    key: tuple[str, str]   # (system_dir, label_dir)
    display: str           # legend label


COMPARISON_SYSTEMS: tuple[SystemSpec, ...] = (
    SystemSpec(("codex", "gpt-5.5"),                "Codex (GPT-5.5)"),
    SystemSpec(("claude", "opus-4.7"),              "Claude Code (Opus 4.7)"),
    SystemSpec(("opencode", "qwen3.5-plus"),        "OpenCode (Qwen 3.5-plus)"),
    SystemSpec(("masta", "qwen3.5-plus"),           "MASTA (Qwen 3.5-plus)"),
)

ABLATION_SYSTEMS: tuple[SystemSpec, ...] = (
    SystemSpec(("masta", "qwen3.5-plus"),            "MASTA (full)"),
    SystemSpec(("masta_no_arbiter", "qwen3.5-plus"), "no Arbiter"),
    SystemSpec(("masta_merged", "qwen3.5-plus"),     "merged roles"),
)


# --- data collection ---------------------------------------------------

@dataclass
class ScoreRow:
    system: str
    label: str
    protocol_id: str
    run_idx: int
    level: str            # "protocol" or "base"
    m1: float | None
    m2: float | None
    m3: float | None
    m4: float | None
    m5: float | None


def _load(score_path: Path) -> dict | None:
    try:
        return json.loads(score_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def collect_scores(runs_dir: Path) -> list[ScoreRow]:
    """Walk runs/<system>/<label>/<protocol>/run_<i>/{score.json,base/score.json}.

    Per (system, label, protocol, run) we prefer the protocol-level
    score.json (M1-M5 all populated). When that's missing we fall back
    to base/score.json but mark M4/M5 as None so they drop out of the
    mean instead of skewing it with the placeholder 0.0.
    """

    rows: list[ScoreRow] = []
    for run_dir in runs_dir.glob("*/*/*/run_1"):
        if not run_dir.is_dir():
            continue
        # path parts: runs / system / label / protocol / run_<i>
        try:
            system = run_dir.parts[-4]
            label = run_dir.parts[-3]
            protocol = run_dir.parts[-2]
            run_name = run_dir.parts[-1]
            run_idx = int(run_name.split("_", 1)[1])
        except (IndexError, ValueError):
            continue

        proto_score = run_dir / "score.json"
        base_score = run_dir / "base" / "score.json"
        data = _load(proto_score) if proto_score.is_file() else None
        level = "protocol" if data is not None else "base"
        if data is None and base_score.is_file():
            data = _load(base_score)
        if data is None:
            continue

        if level == "protocol":
            row = ScoreRow(
                system=system, label=label, protocol_id=protocol,
                run_idx=run_idx, level="protocol",
                m1=data.get("m1"), m2=data.get("m2"), m3=data.get("m3"),
                m4=data.get("m4"), m5=data.get("m5"),
            )
        else:
            # base: only m1/m2/m3 are real; m4/m5 are placeholders. Drop
            # them so they don't poison aggregates.
            row = ScoreRow(
                system=system, label=label, protocol_id=protocol,
                run_idx=run_idx, level="base",
                m1=data.get("m1"), m2=data.get("m2"), m3=data.get("m3"),
                m4=None, m5=None,
            )
        rows.append(row)
    return rows


# --- aggregation -------------------------------------------------------

def _select(rows: Iterable[ScoreRow], specs: Iterable[SystemSpec]) -> dict[tuple[str, str], list[ScoreRow]]:
    out: dict[tuple[str, str], list[ScoreRow]] = {s.key: [] for s in specs}
    for row in rows:
        key = (row.system, row.label)
        if key in out:
            out[key].append(row)
    return out


def _means(
    rows: list[ScoreRow],
    *,
    metric_keys: tuple[str, ...] = METRICS,
) -> tuple[list[float], list[int]]:
    """Return (means, counts) one entry per metric. NaN when no data."""
    means: list[float] = []
    counts: list[int] = []
    for metric in metric_keys:
        values = [getattr(r, metric) for r in rows if getattr(r, metric) is not None]
        if values:
            means.append(sum(values) / len(values))
            counts.append(len(values))
        else:
            means.append(float("nan"))
            counts.append(0)
    return means, counts


# --- plotting ----------------------------------------------------------

def _truncate(label: str, max_len: int = 10) -> str:
    """Shorten ``label`` to ``max_len`` characters, ending with ``...``
    when the original is longer. Used by the heatmap y-axis so long
    protocol ids do not push the figure layout sideways.
    """
    if len(label) <= max_len:
        return label
    return label[: max(0, max_len - 3)] + "..."



def _grouped_bar(
    specs: tuple[SystemSpec, ...],
    rows_by_key: dict[tuple[str, str], list[ScoreRow]],
    title: str,
    out_basename: Path,
    *,
    colors: list[str],
    metric_keys: tuple[str, ...] = METRICS,
) -> None:
    assert len(colors) == len(specs), "one color per spec"
    metric_labels = [METRIC_LABELS[METRICS.index(m)] for m in metric_keys]
    n_metrics = len(metric_keys)
    n_systems = len(specs)
    x = np.arange(n_metrics)
    bar_w = 0.8 / n_systems

    fig, ax = plt.subplots(figsize=(9.0, 4.8))
    fig.patch.set_facecolor("white")
    ax.set_axisbelow(True)
    ax.grid(axis="y", linewidth=0.6, color="#cccccc", alpha=0.7, zorder=0)

    for i, spec in enumerate(specs):
        rows = rows_by_key.get(spec.key, [])
        means, counts = _means(rows, metric_keys=metric_keys)
        offset = (i - (n_systems - 1) / 2) * bar_w
        bars = ax.bar(
            x + offset,
            [m if not math.isnan(m) else 0.0 for m in means],
            width=bar_w,
            label=f"{spec.display} (n={max(counts) if counts else 0})",
            color=colors[i],
            edgecolor="white",
            linewidth=0.6,
            zorder=3,
        )
        for bar, val in zip(bars, means):
            if math.isnan(val):
                continue
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.012,
                f"{val:.2f}",
                ha="center", va="bottom",
                fontsize=7.8,
                color="#222222",
                zorder=4,
            )

    ax.set_xticks(x)
    ax.set_xticklabels(metric_labels, fontsize=11)
    ax.set_ylim(0, 1.10)
    ax.set_yticks(np.linspace(0, 1.0, 6))
    ax.set_ylabel("Score (mean over protocols)", fontsize=11)
    ax.set_title(title, fontsize=12, pad=12)
    legend_ncol = 2 if n_systems > 3 else n_systems
    ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.13),
        ncol=legend_ncol,
        frameon=False,
        fontsize=9.5,
        columnspacing=2.4,
        handletextpad=0.6,
    )
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    fig.tight_layout()
    fig.savefig(out_basename.with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig(out_basename.with_suffix(".png"), dpi=200, bbox_inches="tight")
    plt.close(fig)


def _heatmap(
    specs: tuple[SystemSpec, ...],
    rows_by_key: dict[tuple[str, str], list[ScoreRow]],
    out_basename: Path,
) -> None:
    # Union of protocols seen across all selected systems, sorted by id.
    protocols: list[str] = sorted({
        r.protocol_id for rows in rows_by_key.values() for r in rows
    })
    if not protocols:
        return

    n = len(specs)
    fig, axes = plt.subplots(
        1, n,
        figsize=(3.0 * n + 0.8, max(3.0, len(protocols) * 0.30 + 1.5)),
        sharey=True,
    )
    if n == 1:
        axes = [axes]
    fig.patch.set_facecolor("white")

    for ax, spec in zip(axes, specs):
        rows = rows_by_key.get(spec.key, [])
        # protocol_id -> {metric: score}
        by_proto: dict[str, dict[str, float]] = defaultdict(dict)
        for row in rows:
            for metric in METRICS:
                val = getattr(row, metric)
                if val is not None:
                    by_proto[row.protocol_id][metric] = val
        grid = np.full((len(protocols), len(METRICS)), np.nan)
        for ri, proto in enumerate(protocols):
            for ci, metric in enumerate(METRICS):
                v = by_proto.get(proto, {}).get(metric)
                if v is not None:
                    grid[ri, ci] = v

        im = ax.imshow(
            grid,
            aspect="auto",
            cmap=HEATMAP_CMAP,
            vmin=0.0,
            vmax=1.0,
            interpolation="nearest",
        )
        ax.set_title(spec.display, fontsize=10, pad=6)
        ax.set_xticks(range(len(METRICS)))
        ax.set_xticklabels(METRIC_LABELS, fontsize=9)
        ax.set_yticks(range(len(protocols)))
        ax.set_yticklabels(
            [_truncate(p, 10) for p in protocols],
            fontsize=7,
        )
        # annotate cells
        for ri in range(len(protocols)):
            for ci in range(len(METRICS)):
                v = grid[ri, ci]
                if math.isnan(v):
                    ax.text(ci, ri, "—", ha="center", va="center",
                            fontsize=7, color="grey")
                else:
                    color = "white" if v < 0.5 else "black"
                    ax.text(ci, ri, f"{v:.2f}", ha="center", va="center",
                            fontsize=6.5, color=color)

    # one shared colorbar on the right
    cbar = fig.colorbar(im, ax=axes, fraction=0.018, pad=0.02)
    cbar.set_label("Score", fontsize=9)

    fig.suptitle("Per-protocol scores by system", fontsize=12, y=1.02)
    fig.savefig(out_basename.with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig(out_basename.with_suffix(".png"), dpi=200, bbox_inches="tight")
    plt.close(fig)


def _write_csv(rows: list[ScoreRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["system", "label", "protocol_id", "run_idx", "level",
                         "m1", "m2", "m3", "m4", "m5"])
        for row in sorted(rows, key=lambda r: (r.system, r.label, r.protocol_id, r.run_idx)):
            writer.writerow([
                row.system, row.label, row.protocol_id, row.run_idx, row.level,
                "" if row.m1 is None else f"{row.m1:.6f}",
                "" if row.m2 is None else f"{row.m2:.6f}",
                "" if row.m3 is None else f"{row.m3:.6f}",
                "" if row.m4 is None else f"{row.m4:.6f}",
                "" if row.m5 is None else f"{row.m5:.6f}",
            ])


# --- main --------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--output-dir", type=Path, default=Path("figures"))
    args = parser.parse_args(argv)

    if not args.runs_dir.is_dir():
        print(f"ERROR: runs dir not found: {args.runs_dir}", file=sys.stderr)
        return 2
    args.output_dir.mkdir(parents=True, exist_ok=True)

    rows = collect_scores(args.runs_dir)
    print(f"collected {len(rows)} score rows from {args.runs_dir}")
    if not rows:
        print("nothing to plot", file=sys.stderr)
        return 1

    # Coverage report so the caller knows what each spec resolved to.
    print("\n=== per (system, label) coverage ===")
    print(f"{'system':<22} {'label':<22} {'#rows':<6} {'#protocols':<10} {'levels'}")
    counts: dict[tuple[str, str], list[ScoreRow]] = defaultdict(list)
    for r in rows:
        counts[(r.system, r.label)].append(r)
    for key in sorted(counts):
        rs = counts[key]
        levels = sorted({r.level for r in rs})
        protos = sorted({r.protocol_id for r in rs})
        print(f"{key[0]:<22} {key[1]:<22} {len(rs):<6} {len(protos):<10} {','.join(levels)}")

    cmp_rows = _select(rows, COMPARISON_SYSTEMS)
    abl_rows = _select(rows, ABLATION_SYSTEMS)

    print("\nfigures:")
    # Compare: baselines first, MASTA last on the deepest blue so it
    # visually anchors the chart.
    compare_colors = [
        COLOR_GREY_PURPLE,   # codex  
        COLOR_BLOSSOM_PINK,  # claude
        COLOR_SOFT_BLUE,   # opencode
        COLOR_DEEP_BLUE,     # masta
    ]
    _grouped_bar(
        COMPARISON_SYSTEMS, cmp_rows,
        title="System comparison: M1–M5 mean across protocols",
        out_basename=args.output_dir / "compare",
        colors=compare_colors,
    )
    print(f"  wrote {args.output_dir / 'compare.pdf'} (+ .png)")

    # Ablation: MASTA-full reuses the deep blue from compare.pdf so the
    # reader can recognise it across figures; the two ablated variants
    # take softer hues from the same palette.
    ablation_colors = [
        COLOR_DEEP_BLUE,    # masta full
        COLOR_SOFT_BLUE,    # no arbiter 
        COLOR_LIGHT_PURPLE, # merged roles 
    ]
    # Restrict every system in the ablation chart to the protocol scope
    # the ablated variants actually ran on. The two variants only cover
    # the first few protocols, so without this filter MASTA-full's mean
    # would aggregate over the full 14 protocols while the ablations
    # only over 3 — visually unfair. Take the union of protocols seen
    # across the *non-full* variants and intersect every entry with it.
    ablation_scope: set[str] = set()
    for spec in ABLATION_SYSTEMS[1:]:  # skip ABLATION_SYSTEMS[0] = masta full
        for r in abl_rows.get(spec.key, []):
            ablation_scope.add(r.protocol_id)
    if ablation_scope:
        abl_rows = {
            key: [r for r in rows if r.protocol_id in ablation_scope]
            for key, rows in abl_rows.items()
        }
        print(
            f"\n  ablation protocol scope: {sorted(ablation_scope)} "
            f"(n={len(ablation_scope)})"
        )

    _grouped_bar(
        ABLATION_SYSTEMS, abl_rows,
        title="Ablation: removing Arbiter / merging modeling roles",
        out_basename=args.output_dir / "ablation",
        colors=ablation_colors,
    )
    print(f"  wrote {args.output_dir / 'ablation.pdf'} (+ .png)")

    _heatmap(
        COMPARISON_SYSTEMS, cmp_rows,
        out_basename=args.output_dir / "heatmap",
    )
    print(f"  wrote {args.output_dir / 'heatmap.pdf'} (+ .png)")

    _write_csv(rows, args.output_dir / "scores.csv")
    print(f"  wrote {args.output_dir / 'scores.csv'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
