import json
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from matplotlib.patches import Patch

# === SETUP === #
sns.set_theme(style="whitegrid")


def load_spider2v_tasks(examples_dir: Path):
    tasks = []
    for app_dir in examples_dir.glob("*/*"):
        if app_dir.is_dir():
            json_file = app_dir / f"{app_dir.name}.json"
            if json_file.exists():
                try:
                    with open(json_file, "r") as f:
                        tasks.append(json.load(f))
                except Exception as e:
                    print(f"⚠️ Error reading {json_file}: {e}")
    return tasks


def compute_stats(tasks):
    stats = {
        "task_types": Counter(),
        "auth": Counter(),
        "instruction_types": Counter(),
        "categories": Counter(),
        "action_steps": [],
        "instruction_lengths": {"abstract": [], "verbose": []},
        "related_apps": [],
    }

    for task in tasks:
        tags = set(task.get("tags", []))

        if "cli+gui" in tags:
            stats["task_types"]["CLI + GUI"] += 1
        elif "cli" in tags:
            stats["task_types"]["CLI Only"] += 1
        else:
            stats["task_types"]["GUI Only"] += 1

        stats["auth"]["Authenticated" if "account" in tags else "No Auth"] += 1

        if "instruction" in task:
            length = len(task["instruction"].split())
            if "abstract" in tags:
                stats["instruction_types"]["Abstract"] += 1
                stats["instruction_lengths"]["abstract"].append(length)
            elif "verbose" in tags:
                stats["instruction_types"]["Verbose"] += 1
                stats["instruction_lengths"]["verbose"].append(length)

        for tag in tags - {"cli", "cli+gui", "abstract", "verbose", "account"}:
            stats["categories"][tag] += 1

        stats["related_apps"].append(len(task.get("related_apps", [])))

        # Use action_number if available, otherwise fall back to config length
        if "action_number" in task:
            stats["action_steps"].append(task["action_number"])
        else:
            stats["action_steps"].append(len(task.get("config", [])))

    return stats


def plot_density_histogram(data, title, xlabel, filename, output_path):
    plt.figure(figsize=(6, 4))
    sns.histplot(data, kde=True, stat="density", bins=25)
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel("Density")
    plt.tight_layout()
    plt.savefig(output_path / filename, dpi=300)
    plt.close()


def plot_instruction_lengths_kde(stats, output_path):
    plt.figure(figsize=(6, 4))
    sns.histplot(stats["instruction_lengths"]["abstract"], color="orange", kde=True, label="Abstract", stat="density")
    sns.histplot(stats["instruction_lengths"]["verbose"], color="green", kde=True, label="Verbose", stat="density")
    plt.title("Instruction Lengths (Density)")
    plt.xlabel("Words")
    plt.ylabel("Density")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path / "instruction_lengths_hist.png", dpi=300)
    plt.close()


def plot_static_category_pie(examples_path, output_path):
    category_map = {
        "airbyte": "Data Ingestion & Integration",
        "snowflake": "Data Warehousing",
        "bigquery": "Data Warehousing",
        "superset": "Data Analysis & Visualization",
        "metabase": "Data Analysis & Visualization",
        "jupyter": "Data Analysis & Visualization",
        "excel": "Traditional Data Processing",
        "dagster": "Data Orchestration",
        "airflow": "Data Orchestration",
        "dbt": "Data Transformation",
        "servicenow": "IT Service Management",
    }

    grouped = defaultdict(int)
    for app_dir in examples_path.glob("*/*"):
        if app_dir.is_dir():
            tool = app_dir.parent.name
            category = category_map.get(tool, "Other")
            json_file = app_dir / f"{app_dir.name}.json"
            if json_file.exists():
                grouped[category] += 1

    labels = list(grouped.keys())
    sizes = list(grouped.values())
    colors = plt.get_cmap("tab20").colors[: len(labels)]

    fig, ax = plt.subplots(figsize=(8, 6))
    wedges, texts, autotexts = ax.pie(
        sizes, autopct="%1.1f%%", startangle=90, wedgeprops=dict(width=0.3, edgecolor="w"), colors=colors
    )
    ax.set_title("Spider2-V Task Categories", fontsize=14, fontweight="bold")
    ax.set_aspect("equal")
    legend_patches = [Patch(color=colors[i], label=labels[i]) for i in range(len(labels))]
    plt.legend(handles=legend_patches, title="Categories", loc="center left", bbox_to_anchor=(1, 0.5))
    plt.tight_layout()
    plt.savefig(output_path / "task_categories_static.png", dpi=300)
    plt.close()


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
