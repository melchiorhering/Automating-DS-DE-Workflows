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

    fontdict = {
        "family": "serif",
        "color": "darkred",
        "weight": "normal",
        "size": 16,
    }

    ax1.legend(
        handles=legend_handles,
        labels=inner_labels,
        title="Task Categories",
        loc="center left",
        bbox_to_anchor=(1.05, 0.5),
        fontsize="x-large",
        title_fontsize="x-large",
        frameon=False,
    )

    ax1.set_title(f"Task Categories with used Tools — {len(df)} Total Tasks", fontsize="large", weight="bold")
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


def extended_summary(tasks, stats):
    total_tasks = len(tasks)

    easy = sum(1 for x in stats["action_steps"] if x <= 5)
    medium = sum(1 for x in stats["action_steps"] if 6 <= x <= 15)
    hard = sum(1 for x in stats["action_steps"] if x > 15)

    easy_pct = round((easy / total_tasks) * 100, 1)
    medium_pct = round((medium / total_tasks) * 100, 1)
    hard_pct = round((hard / total_tasks) * 100, 1)

    step_percentiles = sorted(stats["action_steps"])
    n = len(step_percentiles)
    avg_steps = round(sum(step_percentiles) / n, 2)
    percentile_25 = step_percentiles[int(n * 0.25)] if n > 0 else 0
    percentile_50 = step_percentiles[int(n * 0.50)] if n > 0 else 0
    percentile_75 = step_percentiles[int(n * 0.75)] if n > 0 else 0

    return {
        "Total Tasks": f"{total_tasks} (100%)",
        "Pure CLI": f"{stats['task_types']['CLI Only']} ({round(stats['task_types']['CLI Only'] / total_tasks * 100, 1)}%)",
        "Pure GUI": f"{stats['task_types']['GUI Only']} ({round(stats['task_types']['GUI Only'] / total_tasks * 100, 1)}%)",
        "CLI + GUI": f"{stats['task_types']['CLI + GUI']} ({round(stats['task_types']['CLI + GUI'] / total_tasks * 100, 1)}%)",
        "w. Authentic User Account": f"{stats['auth']['Authenticated']} ({round(stats['auth']['Authenticated'] / total_tasks * 100, 1)}%)",
        "w/o. Authentic User Account": f"{stats['auth']['No Auth']} ({round(stats['auth']['No Auth'] / total_tasks * 100, 1)}%)",
        "Easy (≤ 5)": f"{easy} ({easy_pct}%)",
        "Medium (6 ~ 15)": f"{medium} ({medium_pct}%)",
        "Hard (> 15)": f"{hard} ({hard_pct}%)",
        "Avg. Action Steps (P25/P50/P75)": f"{avg_steps} / {percentile_25} / {percentile_50} / {percentile_75}",
        "Avg. Length of Abstract Instructions": round(
            sum(stats["instruction_lengths"]["abstract"]) / len(stats["instruction_lengths"]["abstract"]), 1
        )
        if stats["instruction_lengths"]["abstract"]
        else 0,
        "Avg. Length of Verbose Instructions": round(
            sum(stats["instruction_lengths"]["verbose"]) / len(stats["instruction_lengths"]["verbose"]), 1
        )
        if stats["instruction_lengths"]["verbose"]
        else 0,
        "Avg. Number of Used Apps Per Task": round(sum(stats["related_apps"]) / len(stats["related_apps"]), 1)
        if stats["related_apps"]
        else 0,
    }


def save_extended_summary(tasks, stats, output_path):
    summary = extended_summary(tasks, stats)
    summary_df = pd.DataFrame.from_dict(summary, orient="index", columns=["Value"])
    summary_df.to_latex(output_path / "task_extended_summary_table.tex", header=True, bold_rows=True)
    return summary_df


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
