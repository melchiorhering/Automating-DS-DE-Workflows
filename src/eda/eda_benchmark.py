# src/eda/eda_benchmarks.py
# -------------------------
# CLI :  python -m eda_benchmarks mapping.json src/benchmark/evaluation_examples/examples
# Notebook usage identical to before.

from __future__ import annotations

import colorsys
import json
from collections import Counter
from pathlib import Path
from typing import List, Set

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns  # type: ignore

# ─────────────────────────────────────────────────  GLOBAL STYLE  ───────────────────────────────────────────────
sns.set_theme(style="whitegrid", font_scale=0.95)

PIPELINE_TAGS = [
    "data_ingestion_and_integration",
    "data_warehousing",
    "data_orchestration",
    "data_analysis_and_visualization",
    "traditional_data_processing",
    "it_service_management",
    "data_transformation",
]

EXCLUDE_TOOLS = {"chromium"}

PALETTE_INNER = sns.color_palette("tab10", n_colors=len(PIPELINE_TAGS))


def _lighten(color, frac=0.5):
    r, g, b = color
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    l = 1 - frac * (1 - l)
    return colorsys.hls_to_rgb(h, l, s)


def collect(mapping_path: str | Path, examples_root: str | Path) -> pd.DataFrame:
    mapping = json.loads(Path(mapping_path).read_text())
    examples_root = Path(examples_root)

    rows: list[dict] = []
    missing: Set[str] = set()
    n_found = n_missing = 0

    for tool, uids in mapping.items():
        for uid in uids:
            meta = examples_root / tool / uid / f"{uid}.json"
            if not meta.is_file():
                if str(meta) not in missing:
                    print(f"⚠️ missing {meta}")
                    missing.add(str(meta))
                n_missing += 1
                continue
            data = json.loads(meta.read_text())
            n_found += 1
            rows.append(
                {
                    "tool": tool,
                    "tags": data.get("tags", []),
                    "action_number": data.get("action_number"),
                    "instruction": data.get("instruction", ""),
                    "related_apps": data.get("related_apps", []),
                    "config": data.get("config", []),
                    "evaluator_func": data.get("evaluator", {}).get("func", "—"),
                }
            )

    print(f"\nLoaded {n_found} tasks • {n_missing} missing\n")
    return pd.DataFrame(rows)


def make_plots(df: pd.DataFrame) -> List[plt.Figure]:
    figs: List[plt.Figure] = []
    if df.empty:
        print("No data to plot.")
        return figs

    # ── Donut chart ──
    inner_counter = Counter(t for tags in df["tags"] for t in tags if t in PIPELINE_TAGS)
    inner_raw = [t for t in PIPELINE_TAGS if t in inner_counter]
    inner_counts = [inner_counter[t] for t in inner_raw]
    inner_labels = [t.replace("_", " ").title() for t in inner_raw]
    inner_colors = PALETTE_INNER[: len(inner_raw)]

    tool_counter: Counter[str] = Counter()
    tool_tag_map: dict[str, str] = {}
    for _, row in df.iterrows():
        dom = next((t for t in row.tags if t in PIPELINE_TAGS), None)
        tool = row.tool.lower()
        if tool in EXCLUDE_TOOLS:
            continue
        tool_counter[tool] += 1
        if dom and tool not in tool_tag_map:
            tool_tag_map[tool] = dom

    total_tools = sum(tool_counter.values())
    outer_labels, outer_counts, outer_colors = [], [], []
    for tag, base_color in zip(inner_raw, inner_colors, strict=False):
        for tool, cnt in tool_counter.items():
            if tool_tag_map.get(tool) == tag:
                outer_labels.append(tool.title())
                outer_counts.append(cnt)
                outer_colors.append(_lighten(base_color, 0.5))
    outer_pct = [cnt / total_tools * 100 for cnt in outer_counts]

    fig1, ax1 = plt.subplots(figsize=(9, 8))
    wedges_outer, _ = ax1.pie(
        outer_counts,
        radius=1.0,
        colors=outer_colors,
        startangle=90,
        counterclock=False,
        labels=None,
        wedgeprops=dict(width=0.3, edgecolor="white"),
    )
    for w, lbl, pc in zip(wedges_outer, outer_labels, outer_pct, strict=False):
        ang = (w.theta2 + w.theta1) / 2
        theta_rad = np.deg2rad(ang)
        r = 1.0 - 0.15
        x = r * np.cos(theta_rad)
        y = r * np.sin(theta_rad)
        ax1.text(x, y, f"{lbl}\n{pc:.1f}%", ha="center", va="center", fontsize="small", color="black", weight="bold")

    ax1.pie(
        inner_counts,
        radius=0.65,
        colors=inner_colors,
        startangle=90,
        counterclock=False,
        labels=None,
        wedgeprops=dict(width=0.3, edgecolor="white"),
    )
    ax1.add_artist(plt.Circle((0, 0), 0.35, color="white", zorder=10))

    legend_handles = [plt.Rectangle((0, 0), 1, 1, fc=c) for c in inner_colors]
    ax1.legend(
        handles=legend_handles,
        labels=inner_labels,
        title="Task Categories",
        loc="center left",
        bbox_to_anchor=(1.05, 0.5),
        fontsize="medium",
        title_fontsize="large",
        frameon=False,
    )

    ax1.set_title(
        f"Tasks by Task Category (inner) and Tools (outer) — {len(df)} Total Tasks", fontsize="large", weight="bold"
    )
    ax1.axis("equal")
    figs.append(fig1)

    # ── New distribution-style plots ──
    df["instruction_word_count"] = df["instruction"].apply(lambda x: len(x.split()))
    df["instruction_type"] = df["tags"].apply(
        lambda t: "verbose" if "verbose" in t else ("abstract" if "abstract" in t else "other")
    )
    df["n_related_apps"] = df["related_apps"].apply(lambda x: len(x))

    # Shared horizontal layout for 3 plots
    fig_dist, axes = plt.subplots(ncols=3, figsize=(18, 5), constrained_layout=True)

    # Steps distribution
    sns.histplot(df["action_number"].dropna(), bins=30, stat="probability", kde=True, ax=axes[0], color="#69b3a2")
    axes[0].set_title("Steps", fontsize="medium", weight="bold")
    axes[0].set_ylabel("Frequency", weight="bold")
    axes[0].set_xlabel("Number of Steps", weight="bold")

    # Instruction length distribution
    sns.histplot(
        data=df[df["instruction_type"].isin(["abstract", "verbose"])],
        x="instruction_word_count",
        hue="instruction_type",
        bins=40,
        stat="probability",
        kde=True,
        palette={"abstract": "#e69f00", "verbose": "#56b4e9"},
        ax=axes[1],
        multiple="stack",
    )
    axes[1].set_title("Words", fontsize="medium", weight="bold")
    axes[1].set_ylabel("Frequency", weight="bold")
    axes[1].set_xlabel("Number of Words", weight="bold")

    # Related apps count distribution
    sns.histplot(df["n_related_apps"], bins=range(0, 10), stat="probability", kde=True, ax=axes[2], color="purple")
    axes[2].set_title("Number of Related Apps", fontsize="medium", weight="bold")
    axes[2].set_ylabel("Frequency", weight="bold")
    axes[2].set_xlabel("Related Apps Count", weight="bold")

    figs.append(fig_dist)

    return figs


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Benchmark JSON EDA")
    parser.add_argument("mapping", type=Path, help="tool→uuid list JSON")
    parser.add_argument("examples_root", type=Path, help="evaluation_examples/examples/")
    args = parser.parse_args()
    df = collect(args.mapping, args.examples_root)
    figs = make_plots(df)
    for f in figs:
        f.tight_layout()
    if figs:
        plt.show()
