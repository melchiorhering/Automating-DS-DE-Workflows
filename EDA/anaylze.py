import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import matplotlib.patheffects as path_effects
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from IPython.display import HTML, display

# Set the plot theme to a light theme with better aesthetics
sns.set_theme(style="whitegrid", palette="pastel")

# Improve matplotlib default settings with more modern styling
plt.rcParams["figure.figsize"] = (12, 8)  # Larger figure size
plt.rcParams["font.size"] = 12  # Larger font size
plt.rcParams["axes.titlesize"] = 18  # Even larger title
plt.rcParams["axes.labelsize"] = 14  # Larger axis labels
plt.rcParams["xtick.labelsize"] = 12  # Larger tick labels
plt.rcParams["ytick.labelsize"] = 12  # Larger tick labels
plt.rcParams["axes.spines.top"] = False  # Remove top spine
plt.rcParams["axes.spines.right"] = False  # Remove right spine
plt.rcParams["figure.facecolor"] = "white"  # White figure background
plt.rcParams["axes.facecolor"] = "#f8f9fa"  # Light gray plot background
plt.rcParams["axes.grid"] = True  # Show grid
plt.rcParams["grid.alpha"] = 0.3  # Light grid
plt.rcParams["legend.frameon"] = True  # Add frame to legend
plt.rcParams["legend.framealpha"] = 0.8  # Semi-transparent legend
plt.rcParams["legend.edgecolor"] = "lightgray"  # Light border for legend
plt.rcParams["legend.facecolor"] = "white"  # White background for legend
plt.rcParams["axes.prop_cycle"] = plt.cycler(
    color=["#3B71CA", "#14A44D", "#E4A11B", "#DC4C64", "#9C27B0", "#3E4551", "#1EA5FC", "#FF8F00"]
)  # More vibrant color palette


class BenchmarkAnalyzer:
    """
    Enhanced analyzer for Spider2-V benchmarking data with improved file type insights
    and connection visualization.
    """

    def __init__(self, root_dir: str):
        """
        Initialize the analyzer with the root directory.

        Parameters:
            root_dir (str): Path to the directory containing benchmark data
        """
        self.root_dir = Path(root_dir)
        self.tools = self._get_tools()
        self.benchmarks = self._scan_benchmarks()
        self._file_type_cache = None  # Cache for file type analysis
        self._tool_stats_cache = None  # Cache for tool statistics

    def _get_tools(self) -> List[str]:
        """Get list of tools (subdirectories in root)."""
        return [d.name for d in self.root_dir.iterdir() if d.is_dir()]

    def _scan_benchmarks(self) -> Dict[str, List[Dict]]:
        """Scan all benchmarks and organize by tool."""
        benchmarks = {}

        for tool in self.tools:
            tool_dir = self.root_dir / tool
            benchmarks[tool] = []

            for benchmark_dir in tool_dir.iterdir():
                if benchmark_dir.is_dir():
                    benchmark_id = benchmark_dir.name
                    benchmark_data = {
                        "id": benchmark_id,
                        "path": str(benchmark_dir),
                        "files": [f.name for f in benchmark_dir.iterdir()],
                    }

                    # Load JSON files
                    for json_file in benchmark_dir.glob("*.json"):
                        try:
                            with open(json_file, "r", encoding="utf-8") as f:
                                file_key = json_file.stem
                                benchmark_data[file_key] = json.load(f)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass

                    benchmarks[tool].append(benchmark_data)

        return benchmarks

    def get_summary(self) -> Dict[str, Any]:
        """Get overall summary statistics."""
        total_benchmarks = sum(len(benchmarks) for benchmarks in self.benchmarks.values())

        # Count file types across all tools
        all_file_types = Counter()
        for tool in self.tools:
            tool_file_types = self._count_file_types(tool)
            all_file_types.update(tool_file_types)

        # Count JSON configurations
        json_configs = 0
        connection_configs = 0

        for tool, benchmarks in self.benchmarks.items():
            for benchmark in benchmarks:
                # Count benchmark ID JSONs
                if benchmark["id"] in benchmark:
                    json_configs += 1

                # Count connection JSONs
                if "connection" in benchmark:
                    connection_configs += 1

        return {
            "total_tools": len(self.tools),
            "total_benchmarks": total_benchmarks,
            "benchmarks_per_tool": {tool: len(benchmarks) for tool, benchmarks in self.benchmarks.items()},
            "file_types": dict(all_file_types),
            "json_configs": json_configs,
            "connection_configs": connection_configs,
        }

    def get_tool_summary(self) -> pd.DataFrame:
        """Get a summary DataFrame for all tools."""
        if self._tool_stats_cache is not None:
            return self._tool_stats_cache

        tool_stats = []

        for tool, tool_benchmarks in self.benchmarks.items():
            # Count file types
            file_types = self._count_file_types(tool)

            # Count JSON files
            json_files = sum(1 for benchmark in tool_benchmarks for f in benchmark["files"] if f.endswith(".json"))

            # Count script files
            script_files = sum(1 for benchmark in tool_benchmarks for f in benchmark["files"] if f.endswith(".sh"))

            # Count examples with connections
            with_connection = sum(1 for benchmark in tool_benchmarks if "connection" in benchmark)

            # Count different file types
            html_files = sum(1 for benchmark in tool_benchmarks for f in benchmark["files"] if f.endswith(".html"))
            md_files = sum(1 for benchmark in tool_benchmarks for f in benchmark["files"] if f.endswith(".md"))
            txt_files = sum(1 for benchmark in tool_benchmarks for f in benchmark["files"] if f.endswith(".txt"))
            zip_files = sum(1 for benchmark in tool_benchmarks for f in benchmark["files"] if f.endswith(".zip"))
            csv_files = sum(1 for benchmark in tool_benchmarks for f in benchmark["files"] if f.endswith(".csv"))
            jsonl_files = sum(1 for benchmark in tool_benchmarks for f in benchmark["files"] if f.endswith(".jsonl"))

            stats = {
                "tool": tool,
                "benchmark_count": len(tool_benchmarks),
                "total_files": sum(len(benchmark["files"]) for benchmark in tool_benchmarks),
                "json_files": json_files,
                "script_files": script_files,
                "html_files": html_files,
                "md_files": md_files,
                "txt_files": txt_files,
                "zip_files": zip_files,
                "csv_files": csv_files,
                "jsonl_files": jsonl_files,
                "with_connection": with_connection,
                "file_extensions": ", ".join(sorted(file_types.keys())),
            }
            tool_stats.append(stats)

        self._tool_stats_cache = pd.DataFrame(tool_stats)
        return self._tool_stats_cache

    def _count_file_types(self, tool: str) -> Dict[str, int]:
        """Count file extensions for a tool."""
        extensions = []

        for benchmark in self.benchmarks[tool]:
            for file in benchmark["files"]:
                _, ext = os.path.splitext(file)
                if ext:
                    extensions.append(ext.lower())

        return dict(Counter(extensions))

    def analyze_file_types(self) -> pd.DataFrame:
        """
        Analyze file types across all tools.

        Returns:
            DataFrame with file type analysis
        """
        if self._file_type_cache is not None:
            return self._file_type_cache

        file_type_data = []

        for tool, tool_benchmarks in self.benchmarks.items():
            # Get file types for this tool
            file_types = self._count_file_types(tool)

            # Calculate total files
            total_files = sum(file_types.values())

            # Add data for each file type
            for ext, count in file_types.items():
                percentage = (count / total_files * 100) if total_files > 0 else 0

                file_type_data.append(
                    {"tool": tool, "file_type": ext, "count": count, "percentage": round(percentage, 2)}
                )

        self._file_type_cache = pd.DataFrame(file_type_data)
        return self._file_type_cache

    def get_file_type_patterns(self) -> pd.DataFrame:
        """
        Analyze patterns in file type distributions across tools.

        Returns:
            DataFrame with patterns in file type usage
        """
        # Get file type data
        file_data = self.analyze_file_types()

        # Create a pivot table for easier analysis
        pivot = file_data.pivot_table(index="tool", columns="file_type", values="percentage", fill_value=0)

        # Calculate statistics
        tool_summary = []

        for tool in pivot.index:
            tool_row = pivot.loc[tool]

            # Get the primary file types (highest percentage)
            primary_file_type = tool_row.idxmax()
            primary_percentage = tool_row.max()

            # Get the secondary file type
            secondary_types = tool_row.sort_values(ascending=False)
            secondary_file_type = secondary_types.index[1] if len(secondary_types) > 1 else "none"
            secondary_percentage = secondary_types.iloc[1] if len(secondary_types) > 1 else 0

            # Calculate diversity (number of different file types)
            file_type_count = sum(1 for p in tool_row if p > 0)

            # Calculate evenness (how evenly distributed are the file types)
            non_zero_percentages = [p for p in tool_row if p > 0]
            evenness = np.std(non_zero_percentages) if non_zero_percentages else 0

            tool_summary.append(
                {
                    "tool": tool,
                    "primary_file_type": primary_file_type,
                    "primary_percentage": round(primary_percentage, 2),
                    "secondary_file_type": secondary_file_type,
                    "secondary_percentage": round(secondary_percentage, 2),
                    "file_type_count": file_type_count,
                    "file_type_evenness": round(evenness, 2),
                }
            )

        return pd.DataFrame(tool_summary)

    def analyze_file_content_types(self) -> Dict[str, Dict[str, Set[str]]]:
        """
        Analyze what kind of content each tool typically stores in each file type.

        Returns:
            Nested dictionary mapping tools to file types to content pattern descriptions
        """
        # Pattern detection for common files
        content_patterns = {}

        for tool, benchmarks in self.benchmarks.items():
            content_patterns[tool] = defaultdict(set)

            for benchmark in benchmarks:
                benchmark_dir = Path(benchmark["path"])

                # Check JSON files for content patterns
                for json_file in benchmark_dir.glob("*.json"):
                    if json_file.name == "connection.json":
                        content_patterns[tool][".json"].add("connection_config")
                    elif json_file.stem == benchmark["id"]:
                        content_patterns[tool][".json"].add("benchmark_config")
                    else:
                        content_patterns[tool][".json"].add("other_config")

                # Check TXT files
                for txt_file in benchmark_dir.glob("*.txt"):
                    if txt_file.name == "verbose_instruction.txt":
                        content_patterns[tool][".txt"].add("instruction")
                    elif "information" in txt_file.name.lower():
                        content_patterns[tool][".txt"].add("information")
                    elif "schema" in txt_file.name.lower():
                        content_patterns[tool][".txt"].add("schema")

                # Check shell scripts
                for sh_file in benchmark_dir.glob("*.sh"):
                    if sh_file.name == "init.sh":
                        content_patterns[tool][".sh"].add("initialization")
                    elif sh_file.name == "eval.sh":
                        content_patterns[tool][".sh"].add("evaluation")

                # Check for HTML/MD files
                for html_file in benchmark_dir.glob("*.html"):
                    if "retrieved" in html_file.name:
                        content_patterns[tool][".html"].add("retrieval_visualization")

                # Check for data files
                for csv_file in benchmark_dir.glob("*.csv"):
                    if "gold" in csv_file.name:
                        content_patterns[tool][".csv"].add("gold_data")
                    else:
                        content_patterns[tool][".csv"].add("data")

                # Check for JSONL files
                for jsonl_file in benchmark_dir.glob("*.jsonl"):
                    content_patterns[tool][".jsonl"].add("data")

        # Convert defaultdict to regular dict
        return {tool: dict(patterns) for tool, patterns in content_patterns.items()}

    def get_file_signature_by_tool(self) -> pd.DataFrame:
        """
        Generate a "file signature" for each tool - the typical set of files found in benchmarks.

        Returns:
            DataFrame with file signatures by tool
        """
        signatures = []

        for tool, benchmarks in self.benchmarks.items():
            # Count occurrences of each file
            file_counts = Counter()
            total_benchmarks = len(benchmarks)

            for benchmark in benchmarks:
                file_counts.update(benchmark["files"])

            # Calculate frequency of each file
            file_freqs = {file: round(count / total_benchmarks * 100, 2) for file, count in file_counts.items()}

            # Find common files (present in >80% of benchmarks)
            common_files = [file for file, freq in file_freqs.items() if freq >= 80]

            # Find semi-common files (present in 40-80% of benchmarks)
            semi_common = [file for file, freq in file_freqs.items() if 40 <= freq < 80]

            # Find top 3 most common file extensions
            extensions = Counter()
            for benchmark in benchmarks:
                for file in benchmark["files"]:
                    _, ext = os.path.splitext(file)
                    if ext:
                        extensions[ext.lower()] += 1

            top_extensions = [ext for ext, _ in extensions.most_common(3)]

            signatures.append(
                {
                    "tool": tool,
                    "benchmark_count": total_benchmarks,
                    "avg_files_per_benchmark": round(sum(len(b["files"]) for b in benchmarks) / total_benchmarks, 2),
                    "common_files": ", ".join(sorted(common_files)),
                    "semi_common_files": ", ".join(sorted(semi_common)),
                    "top_extensions": ", ".join(top_extensions),
                    "unique_file_count": len(file_counts),
                }
            )

        return pd.DataFrame(signatures)

    def plot_file_type_distribution(self) -> None:
        """Plot detailed distribution of file types across tools with enhanced styling."""
        # Get file type analysis data
        file_data = self.analyze_file_types()

        # Create pivot table for heatmap
        pivot = file_data.pivot_table(index="tool", columns="file_type", values="percentage", fill_value=0)

        # Sort tools by total file count
        tool_summary = self.get_tool_summary()
        tool_order = tool_summary.sort_values("total_files", ascending=False)["tool"].tolist()

        # Sort file types by overall frequency
        file_type_totals = file_data.groupby("file_type")["count"].sum().sort_values(ascending=False)
        file_type_order = file_type_totals.index.tolist()

        # Filter to top file types for readability
        top_file_types = file_type_totals.head(10).index.tolist()
        pivot_filtered = pivot[top_file_types].loc[tool_order]

        # Create figure
        plt.figure(figsize=(14, 8))

        # Create heatmap with percentage values
        ax = sns.heatmap(
            pivot_filtered,
            annot=True,
            fmt=".1f",
            cmap="YlGnBu",
            linewidths=0.5,
            cbar_kws={"label": "Percentage of Files"},
            vmin=0,
            vmax=min(100, pivot_filtered.values.max() * 1.2),  # Cap at 100% or 20% higher than max
        )

        # Enhance styling
        plt.title("File Type Distribution by Tool (%)", fontsize=18, fontweight="bold", pad=20)
        plt.xlabel("File Type", fontsize=14, fontweight="bold")
        plt.ylabel("Tool", fontsize=14, fontweight="bold")

        # Improved colorbar
        cbar = ax.collections[0].colorbar
        cbar.ax.tick_params(labelsize=10)
        cbar.set_label("Percentage of Files", fontsize=12, fontweight="bold")

        plt.tight_layout()
        plt.show()

        # Create a second visualization showing absolute counts
        plt.figure(figsize=(16, 8))

        # Prepare data for grouped bar chart - select top 6 file types for clarity
        top6_types = file_type_totals.head(6).index.tolist()
        chart_data = file_data[file_data["file_type"].isin(top6_types)]

        # Sort tools by benchmark count
        tool_order = tool_summary.sort_values("benchmark_count", ascending=False)["tool"].tolist()
        chart_data["tool"] = pd.Categorical(chart_data["tool"], categories=tool_order, ordered=True)
        chart_data = chart_data.sort_values(["tool", "count"], ascending=[True, False])

        # Create a grouped bar chart
        ax = sns.barplot(
            x="tool", y="count", hue="file_type", data=chart_data, palette="tab10", edgecolor="white", linewidth=0.8
        )

        # Add a subtle shadow effect to the bars
        for patch in ax.patches:
            patch.set_path_effects([path_effects.SimpleLineShadow(offset=(1, -1), alpha=0.2), path_effects.Normal()])

        # Enhance title and labels
        plt.title("File Count by Type and Tool", fontsize=18, fontweight="bold", pad=20)
        plt.xlabel("Tool", fontsize=14, fontweight="bold")
        plt.ylabel("Number of Files", fontsize=14, fontweight="bold")

        # Improve the x-axis labels
        plt.xticks(rotation=45, ha="right", fontsize=12)

        # Enhance the legend
        legend = plt.legend(
            title="File Types",
            title_fontsize=12,
            fontsize=10,
            bbox_to_anchor=(1.02, 1),
            loc="upper left",
            borderaxespad=0,
            frameon=True,
            framealpha=0.95,
            edgecolor="lightgray",
        )
        legend.get_title().set_fontweight("bold")

        # Add a subtle grid for the y-axis only
        ax.yaxis.grid(True, linestyle="--", alpha=0.3)
        ax.xaxis.grid(False)

        plt.tight_layout()
        plt.show()

    def plot_connection_analysis(self) -> None:
        """Create improved connection analysis visualization that works with multiple tools."""
        # Get connection data
        connections = self.analyze_connections()

        # If there are no connections, inform the user
        if connections.empty:
            print("No connection configurations found in any tool.")
            return

        # Count connections by tool
        conn_by_tool = connections.groupby("tool").size().reset_index(name="connection_count")

        # Calculate connection coverage
        tool_summary = self.get_tool_summary()
        conn_coverage = []

        # Make sure we have data for all tools, even those without connections
        for tool in self.tools:
            if tool in conn_by_tool["tool"].values:
                conn_count = conn_by_tool.loc[conn_by_tool["tool"] == tool, "connection_count"].iloc[0]
            else:
                conn_count = 0

            if tool in tool_summary["tool"].values:
                benchmark_count = tool_summary.loc[tool_summary["tool"] == tool, "benchmark_count"].iloc[0]
            else:
                benchmark_count = 0

            coverage = (conn_count / benchmark_count * 100) if benchmark_count > 0 else 0

            conn_coverage.append(
                {
                    "tool": tool,
                    "connection_count": conn_count,
                    "benchmark_count": benchmark_count,
                    "coverage_percent": round(coverage, 2),
                }
            )

        conn_coverage_df = pd.DataFrame(conn_coverage)

        # Filter to only show tools with benchmarks
        conn_coverage_df = conn_coverage_df[conn_coverage_df["benchmark_count"] > 0]

        # Check if we have more than one tool with connections
        tools_with_connections = conn_coverage_df[conn_coverage_df["connection_count"] > 0]["tool"].nunique()

        if tools_with_connections <= 1:
            # Special case for only one tool with connections
            tool_with_connections = conn_coverage_df[conn_coverage_df["connection_count"] > 0]["tool"].iloc[0]

            plt.figure(figsize=(12, 6))

            # Create a bar plot showing connection coverage for all tools
            ax = sns.barplot(
                x="tool",
                y="coverage_percent",
                data=conn_coverage_df,
                palette="Blues_d",
                edgecolor="white",
                linewidth=1.5,
                alpha=0.7,
            )

            # Highlight the tool with connections
            bar_colors = ["#3B71CA" if t == tool_with_connections else "#D3D3D3" for t in conn_coverage_df["tool"]]

            for i, patch in enumerate(ax.patches):
                patch.set_facecolor(bar_colors[i])

            # Add data labels
            for i, row in conn_coverage_df.iterrows():
                ax.text(
                    i,
                    row["coverage_percent"] + 1,
                    f"{row['coverage_percent']}%",
                    ha="center",
                    va="bottom",
                    fontsize=10,
                    fontweight="bold" if row["tool"] == tool_with_connections else "normal",
                    color="black",
                )

                # Add connection count below the tool name
                ax.text(
                    i,
                    -5,
                    f"{int(row['connection_count'])}/{int(row['benchmark_count'])}",
                    ha="center",
                    va="top",
                    fontsize=9,
                    color="#555555",
                )

            plt.title(
                f"Connection Coverage by Tool\n(Only {tool_with_connections} has connection configurations)",
                fontsize=16,
                fontweight="bold",
                pad=20,
            )
            plt.xlabel("Tool", fontsize=12, fontweight="bold")
            plt.ylabel("Connection Coverage (%)", fontsize=12, fontweight="bold")
            plt.xticks(rotation=45, ha="right")

            # Set y-axis limit to show percentages
            plt.ylim(0, min(105, conn_coverage_df["coverage_percent"].max() * 1.2))

            # Add subtle grid
            ax.yaxis.grid(True, linestyle="--", alpha=0.3)
            ax.xaxis.grid(False)

            plt.tight_layout()
            plt.show()

            # Add a text note about the single tool issue
            print(f"Note: Only {tool_with_connections} contains connection configurations in this dataset.")
            print(
                f"Found {conn_coverage_df.loc[conn_coverage_df['tool'] == tool_with_connections, 'connection_count'].iloc[0]} connections in {conn_coverage_df.loc[conn_coverage_df['tool'] == tool_with_connections, 'benchmark_count'].iloc[0]} benchmarks."
            )

            # If the tool with connections is airbyte, provide additional analysis on connection types
            if tool_with_connections == "airbyte" and not connections.empty:
                # Analyze source and destination patterns
                plt.figure(figsize=(14, 6))

                # Extract source and destination from names if available
                connection_patterns = []

                for _, conn in connections.iterrows():
                    name = conn["name"]
                    if "→" in name:
                        parts = name.split("→")
                        source = parts[0].strip()
                        destination = parts[1].strip() if len(parts) > 1 else ""

                        connection_patterns.append({"source": source, "destination": destination})

                if connection_patterns:
                    patterns_df = pd.DataFrame(connection_patterns)

                    # Count sources and destinations
                    source_counts = patterns_df["source"].value_counts().reset_index()
                    source_counts.columns = ["source", "count"]

                    dest_counts = patterns_df["destination"].value_counts().reset_index()
                    dest_counts.columns = ["destination", "count"]

                    # Plot top sources and destinations
                    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))

                    # Plot top sources
                    top_sources = source_counts.head(10)
                    sns.barplot(
                        x="count",
                        y="source",
                        data=top_sources,
                        palette="Blues_d",
                        edgecolor="white",
                        linewidth=1.2,
                        ax=ax1,
                    )

                    ax1.set_title("Top Connection Sources", fontsize=14, fontweight="bold")
                    ax1.set_xlabel("Count", fontsize=12)
                    ax1.set_ylabel("Source", fontsize=12)

                    # Plot top destinations
                    top_dests = dest_counts.head(10)
                    sns.barplot(
                        x="count",
                        y="destination",
                        data=top_dests,
                        palette="Greens_d",
                        edgecolor="white",
                        linewidth=1.2,
                        ax=ax2,
                    )

                    ax2.set_title("Top Connection Destinations", fontsize=14, fontweight="bold")
                    ax2.set_xlabel("Count", fontsize=12)
                    ax2.set_ylabel("Destination", fontsize=12)

                    plt.tight_layout()
                    plt.suptitle(
                        f"Connection Patterns in {tool_with_connections}", fontsize=16, fontweight="bold", y=1.05
                    )
                    plt.show()
        else:
            # Regular case with multiple tools having connections
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 7))

            # Plot 1: Raw connection counts
            sns.barplot(
                x="tool",
                y="connection_count",
                data=conn_coverage_df,
                ax=ax1,
                palette="Blues_d",
                edgecolor="white",
                linewidth=1.5,
            )

            ax1.set_title("Number of Connections per Tool", fontsize=14, fontweight="bold")
            ax1.set_xlabel("Tool", fontsize=12)
            ax1.set_ylabel("Connection Count", fontsize=12)
            ax1.tick_params(axis="x", rotation=45)

            # Add data labels
            for i, v in enumerate(conn_coverage_df["connection_count"]):
                ax1.text(i, v + 0.1, str(int(v)), ha="center", fontweight="bold", color="#333333")

            # Plot 2: Coverage percentage
            sns.barplot(
                x="tool",
                y="coverage_percent",
                data=conn_coverage_df,
                ax=ax2,
                palette="Greens_d",
                edgecolor="white",
                linewidth=1.5,
            )

            ax2.set_title("Connection Coverage (% of Benchmarks with Connections)", fontsize=14, fontweight="bold")
            ax2.set_xlabel("Tool", fontsize=12)
            ax2.set_ylabel("Coverage Percentage", fontsize=12)
            ax2.tick_params(axis="x", rotation=45)
            ax2.set_ylim(0, min(105, conn_coverage_df["coverage_percent"].max() * 1.2))

            # Add data labels with percentage
            for i, v in enumerate(conn_coverage_df["coverage_percent"]):
                ax2.text(i, v + 1, f"{v}%", ha="center", fontweight="bold", color="#333333")

            plt.suptitle("Connection Configuration Analysis by Tool", fontsize=16, fontweight="bold")

            plt.tight_layout()
            plt.subplots_adjust(top=0.85)
            plt.show()

    def visualize_benchmark_structure(self) -> None:
        """
        Visualize the typical file structure of benchmarks across tools.
        """
        # Get file signature information
        signatures = self.get_file_signature_by_tool()

        # Create file extension heatmap
        file_data = self.analyze_file_types()

        # Count benchmark-level stats
        tool_file_counts = []

        for tool, benchmarks in self.benchmarks.items():
            for benchmark in benchmarks:
                # Count files by extension in this benchmark
                ext_counts = Counter()
                for file in benchmark["files"]:
                    _, ext = os.path.splitext(file)
                    if ext:
                        ext_counts[ext.lower()] += 1

                # Get common file patterns
                has_json = any(f.endswith(".json") for f in benchmark["files"])
                has_sh = any(f.endswith(".sh") for f in benchmark["files"])
                has_md = any(f.endswith(".md") for f in benchmark["files"])
                has_html = any(f.endswith(".html") for f in benchmark["files"])
                has_txt = any(f.endswith(".txt") for f in benchmark["files"])

                # Store benchmark stats
                tool_file_counts.append(
                    {
                        "tool": tool,
                        "benchmark_id": benchmark["id"],
                        "file_count": len(benchmark["files"]),
                        "json_count": ext_counts.get(".json", 0),
                        "sh_count": ext_counts.get(".sh", 0),
                        "md_count": ext_counts.get(".md", 0),
                        "html_count": ext_counts.get(".html", 0),
                        "txt_count": ext_counts.get(".txt", 0),
                        "has_json": has_json,
                        "has_sh": has_sh,
                        "has_md": has_md,
                        "has_html": has_html,
                        "has_txt": has_txt,
                    }
                )

        benchmark_stats = pd.DataFrame(tool_file_counts)

        # Plot 1: File count distribution by tool
        plt.figure(figsize=(12, 6))

        # Create violin plots showing file count distribution
        ax = sns.violinplot(x="tool", y="file_count", data=benchmark_stats, palette="Set3", inner="box", cut=0)

        # Add data points for individual benchmarks
        sns.stripplot(x="tool", y="file_count", data=benchmark_stats, size=3, color="black", alpha=0.3, jitter=True)

        plt.title("Distribution of File Counts per Benchmark by Tool", fontsize=16, fontweight="bold", pad=20)
        plt.xlabel("Tool", fontsize=12, fontweight="bold")
        plt.ylabel("Number of Files", fontsize=12, fontweight="bold")
        plt.xticks(rotation=45, ha="right")

        # Add subtle grid
        ax.yaxis.grid(True, linestyle="--", alpha=0.3)
        ax.xaxis.grid(False)

        plt.tight_layout()
        plt.show()

        # Plot 2: File presence heatmap
        presence_cols = ["has_json", "has_sh", "has_md", "has_html", "has_txt"]
        presence_agg = benchmark_stats.groupby("tool")[presence_cols].mean().reset_index()

        # Melt for heatmap format
        presence_melted = presence_agg.melt(
            id_vars=["tool"], value_vars=presence_cols, var_name="file_type", value_name="presence_rate"
        )

        # Clean up file type names for display
        presence_melted["file_type"] = presence_melted["file_type"].str.replace("has_", ".").str.lower()

        # Create a pivot table
        presence_pivot = presence_melted.pivot(index="tool", columns="file_type", values="presence_rate")

        # Sort tools by total file counts
        tool_order = signatures.sort_values("avg_files_per_benchmark", ascending=False)["tool"].tolist()
        presence_pivot = presence_pivot.loc[tool_order]

        # Create heatmap
        plt.figure(figsize=(12, 8))

        ax = sns.heatmap(
            presence_pivot,
            annot=True,
            fmt=".0%",
            cmap="YlGnBu",
            linewidths=0.5,
            cbar_kws={"label": "Presence Rate (%)", "format": "%.0f%%"},
            vmin=0,
            vmax=1,
        )

        plt.title("File Type Presence Rate by Tool", fontsize=16, fontweight="bold", pad=20)
        plt.xlabel("File Extension", fontsize=12, fontweight="bold")
        plt.ylabel("Tool", fontsize=12, fontweight="bold")

        # Improve colorbar
        cbar = ax.collections[0].colorbar
        cbar.ax.tick_params(labelsize=10)
        cbar.set_label("Presence Rate", fontsize=12, fontweight="bold")

        plt.tight_layout()
        plt.show()

        # Plot 3: Average file counts by type and tool
        # Calculate average counts by tool
        avg_counts = (
            benchmark_stats.groupby("tool")[["json_count", "sh_count", "md_count", "html_count", "txt_count"]]
            .mean()
            .reset_index()
        )

        # Melt for easier plotting
        avg_counts_melted = avg_counts.melt(
            id_vars=["tool"],
            value_vars=["json_count", "sh_count", "md_count", "html_count", "txt_count"],
            var_name="file_type",
            value_name="avg_count",
        )

        # Clean up file type names
        avg_counts_melted["file_type"] = avg_counts_melted["file_type"].str.replace("_count", "")

        # Map to proper extensions
        extension_map = {"json": ".json", "sh": ".sh", "md": ".md", "html": ".html", "txt": ".txt"}
        avg_counts_melted["file_type"] = avg_counts_melted["file_type"].map(extension_map)

        # Create grouped bar chart
        plt.figure(figsize=(14, 7))

        ax = sns.barplot(
            x="tool",
            y="avg_count",
            hue="file_type",
            data=avg_counts_melted,
            palette="Set2",
            edgecolor="white",
            linewidth=0.8,
        )

        plt.title("Average Number of Files per Benchmark by Type and Tool", fontsize=16, fontweight="bold", pad=20)
        plt.xlabel("Tool", fontsize=12, fontweight="bold")
        plt.ylabel("Average Count", fontsize=12, fontweight="bold")
        plt.xticks(rotation=45, ha="right")

        # Enhance legend
        plt.legend(title="File Type", title_fontsize=12, fontsize=10, frameon=True)

        # Add subtle grid
        ax.yaxis.grid(True, linestyle="--", alpha=0.3)
        ax.xaxis.grid(False)

        plt.tight_layout()
        plt.show()

    def analyze_file_type_insights(self) -> pd.DataFrame:
        """
        Provide detailed insights about file types and their roles in the benchmarks.

        Returns:
            DataFrame with file type insights
        """
        # Get file pattern data
        file_data = self.analyze_file_types()
        tool_data = self.get_tool_summary()

        # Extract content type patterns
        content_patterns = self.analyze_file_content_types()

        # Process content patterns into a more structured format
        content_roles = defaultdict(lambda: defaultdict(list))

        for tool, type_patterns in content_patterns.items():
            for file_type, patterns in type_patterns.items():
                for pattern in patterns:
                    content_roles[file_type][pattern].append(tool)

        # Create insights table
        insights = []

        for file_type in sorted(file_data["file_type"].unique()):
            # Calculate usage stats
            type_data = file_data[file_data["file_type"] == file_type]

            # Tools that use this file type
            tools_with_type = type_data["tool"].nunique()
            tools_with_type_pct = (tools_with_type / len(self.tools)) * 100

            # Total count
            total_count = type_data["count"].sum()

            # Top tool by usage
            top_tool = type_data.sort_values("count", ascending=False)["tool"].iloc[0]
            top_tool_count = type_data[type_data["tool"] == top_tool]["count"].iloc[0]

            # Get content roles for this file type
            roles = []
            if file_type in content_roles:
                for role, tools in content_roles[file_type].items():
                    roles.append(f"{role} ({len(tools)} tool{'s' if len(tools) > 1 else ''})")

            # Determine if this is a primary file type for any tool
            primary_for = []
            for tool in self.tools:
                tool_files = file_data[file_data["tool"] == tool]
                if not tool_files.empty:
                    max_type = tool_files.sort_values("percentage", ascending=False)["file_type"].iloc[0]
                    if max_type == file_type:
                        primary_for.append(tool)

            insights.append(
                {
                    "file_type": file_type,
                    "total_count": total_count,
                    "tools_using": tools_with_type,
                    "tools_using_pct": round(tools_with_type_pct, 1),
                    "top_tool": top_tool,
                    "top_tool_count": top_tool_count,
                    "primary_for_tools": ", ".join(primary_for) if primary_for else "None",
                    "typical_roles": "; ".join(roles) if roles else "Unknown",
                    "avg_per_benchmark": round(total_count / tool_data["benchmark_count"].sum(), 2),
                }
            )

        # Sort by total count
        insights_df = pd.DataFrame(insights).sort_values("total_count", ascending=False)
        return insights_df

    def display_file_type_report(self) -> None:
        """
        Display a comprehensive report on file types and their usage patterns.
        """
        # Get insights data
        insights = self.analyze_file_type_insights()

        # Display as HTML for better formatting
        html_output = f"""
        <h2>File Type Analysis Report</h2>
        <p>Total Tools: {len(self.tools)} | Total Benchmarks: {self.get_tool_summary()["benchmark_count"].sum()}</p>

        <h3>Key File Types Overview</h3>
        <table border="1" class="dataframe" style="width:100%; border-collapse: collapse;">
          <thead>
            <tr style="background-color: #f2f2f2;">
              <th>File Type</th>
              <th>Count</th>
              <th>Tools Using</th>
              <th>Primary For</th>
              <th>Top Tool</th>
              <th>Typical Roles</th>
            </tr>
          </thead>
          <tbody>
        """

        # Add rows for each file type
        for _, row in insights.iterrows():
            html_output += f"""
            <tr>
              <td><b>{row["file_type"]}</b></td>
              <td>{row["total_count"]}</td>
              <td>{row["tools_using"]} ({row["tools_using_pct"]}%)</td>
              <td>{row["primary_for_tools"]}</td>
              <td>{row["top_tool"]} ({row["top_tool_count"]})</td>
              <td>{row["typical_roles"]}</td>
            </tr>
            """

        html_output += """
          </tbody>
        </table>

        <h3>Key Insights:</h3>
        <ul>
        """

        # Add insights about file types
        # First, check which file types are universal (used by all tools)
        universal_types = insights[insights["tools_using"] == len(self.tools)]["file_type"].tolist()
        if universal_types:
            html_output += f"<li><b>Universal file types</b>: {', '.join(universal_types)} are used by all tools.</li>"

        # Check which file types are tool-specific
        tool_specific = insights[insights["tools_using"] == 1]
        if not tool_specific.empty:
            html_output += "<li><b>Tool-specific file types</b>:"
            for _, row in tool_specific.iterrows():
                html_output += f" {row['file_type']} (only in {row['top_tool']}),"
            html_output = html_output.rstrip(",") + "</li>"

        # Most common file type by count
        top_type = insights.iloc[0]
        html_output += (
            f"<li><b>Most common file type</b>: {top_type['file_type']} with {top_type['total_count']} files.</li>"
        )

        # Add more insights about typical structures
        file_patterns = self.get_file_type_patterns()
        html_output += "<li><b>Dominant file types by tool</b>:"
        for _, row in file_patterns.iterrows():
            html_output += f" {row['tool']} ({row['primary_file_type']}: {row['primary_percentage']}%),"
        html_output = html_output.rstrip(",") + "</li>"

        # File diversity insights
        diverse_tool = file_patterns.sort_values("file_type_count", ascending=False).iloc[0]
        html_output += f"<li><b>Most diverse file structure</b>: {diverse_tool['tool']} uses {diverse_tool['file_type_count']} different file types.</li>"

        # Identify clusters of similar tools
        html_output += "<li><b>Similar tool groups</b> based on file usage patterns:"

        # Simple clustering based on primary file types
        clusters = defaultdict(list)
        for _, row in file_patterns.iterrows():
            clusters[row["primary_file_type"]].append(row["tool"])

        for file_type, tools in clusters.items():
            if len(tools) > 1:
                html_output += f" {', '.join(tools)} (primarily {file_type});"
        html_output += "</li>"

        html_output += """
        </ul>
        """

        # Display HTML
        display(HTML(html_output))

        # Plot distribution visualization
        self.plot_file_type_distribution()

    def get_json_structure_stats(self, tool: Optional[str] = None) -> pd.DataFrame:
        """
        Analyze the structure of JSON files.

        Parameters:
            tool (str, optional): Specific tool to analyze, or all if None

        Returns:
            DataFrame with JSON structure analysis
        """
        json_analysis = []

        tools_to_analyze = [tool] if tool else self.tools

        for t in tools_to_analyze:
            if t not in self.benchmarks:
                continue

            for benchmark in self.benchmarks[t]:
                for file_name in benchmark["files"]:
                    if file_name.endswith(".json"):
                        file_key = os.path.splitext(file_name)[0]

                        if file_key in benchmark and benchmark[file_key]:
                            json_data = benchmark[file_key]

                            # Extract basic properties
                            properties = {
                                "tool": t,
                                "benchmark_id": benchmark["id"],
                                "file_name": file_name,
                                "depth": self._get_json_depth(json_data),
                                "keys": len(json_data) if isinstance(json_data, dict) else 0,
                                "size": len(json.dumps(json_data)),
                            }

                            # Extract common properties if it's a dictionary
                            if isinstance(json_data, dict):
                                for common_key in [
                                    "name",
                                    "sourceId",
                                    "destinationId",
                                    "status",
                                    "scheduleType",
                                    "geography",
                                ]:
                                    properties[common_key] = json_data.get(common_key, "")

                            json_analysis.append(properties)

        return pd.DataFrame(json_analysis)

    def _get_json_depth(self, obj: Any, current_depth: int = 0) -> int:
        """Calculate the max depth of a JSON object."""
        if isinstance(obj, dict):
            if not obj:
                return current_depth
            return max(self._get_json_depth(v, current_depth + 1) for v in obj.values())
        elif isinstance(obj, list):
            if not obj:
                return current_depth
            return max(self._get_json_depth(item, current_depth + 1) for item in obj)
        else:
            return current_depth

    def analyze_connections(self) -> pd.DataFrame:
        """
        Analyze connection configurations.

        Returns:
            DataFrame with connection analysis
        """
        connections = []

        for tool, benchmarks in self.benchmarks.items():
            for benchmark in benchmarks:
                if "connection" in benchmark and benchmark["connection"]:
                    conn = benchmark["connection"]

                    if not isinstance(conn, dict):
                        continue

                    connection_data = {
                        "tool": tool,
                        "benchmark_id": benchmark["id"],
                        "name": conn.get("name", ""),
                        "source_id": conn.get("sourceId", ""),
                        "destination_id": conn.get("destinationId", ""),
                        "status": conn.get("status", ""),
                        "schedule_type": conn.get("scheduleType", ""),
                        "geography": conn.get("geography", ""),
                        "namespace_definition": conn.get("namespaceDefinition", ""),
                    }

                    # Extract stream info if available
                    sync_catalog = conn.get("syncCatalog", {})
                    if isinstance(sync_catalog, dict):
                        streams = sync_catalog.get("streams", [])
                        connection_data["stream_count"] = len(streams)

                        # Extract stream names
                        stream_names = []
                        for stream in streams:
                            if isinstance(stream, dict) and "stream" in stream:
                                stream_data = stream["stream"]
                                if isinstance(stream_data, dict) and "name" in stream_data:
                                    stream_names.append(stream_data["name"])

                        connection_data["streams"] = ", ".join(stream_names) if stream_names else ""

                    connections.append(connection_data)

        return pd.DataFrame(connections)

    def analyze_benchmark_content(self, tool: Optional[str] = None) -> pd.DataFrame:
        """
        Analyze file content within benchmarks.

        Parameters:
            tool (str, optional): Specific tool to analyze, or all if None

        Returns:
            DataFrame with benchmark content analysis
        """
        content_analysis = []

        tools_to_analyze = [tool] if tool else self.tools

        for t in tools_to_analyze:
            if t not in self.benchmarks:
                continue

            for benchmark in self.benchmarks[t]:
                benchmark_dir = Path(benchmark["path"])

                # Analyze instruction file if it exists
                instruction_file = benchmark_dir / "verbose_instruction.txt"
                instruction_length = 0
                instruction_words = 0

                if instruction_file.exists():
                    try:
                        with open(instruction_file, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                            instruction_length = len(content)
                            instruction_words = len(re.findall(r"\b\w+\b", content))
                    except Exception:
                        pass

                # Count shell script lines
                shell_lines = 0
                for script_file in ["init.sh", "eval.sh"]:
                    script_path = benchmark_dir / script_file
                    if script_path.exists():
                        try:
                            with open(script_path, "r", encoding="utf-8", errors="ignore") as f:
                                shell_lines += sum(1 for _ in f)
                        except Exception:
                            pass

                # Analyze benchmark data
                analysis = {
                    "tool": t,
                    "benchmark_id": benchmark["id"],
                    "file_count": len(benchmark["files"]),
                    "has_instruction": instruction_file.exists(),
                    "instruction_length": instruction_length,
                    "instruction_words": instruction_words,
                    "shell_script_lines": shell_lines,
                    "json_files": sum(1 for f in benchmark["files"] if f.endswith(".json")),
                    "has_connection": "connection" in benchmark,
                }

                content_analysis.append(analysis)

        return pd.DataFrame(content_analysis)

    def analyze_streams(self) -> pd.DataFrame:
        """
        Extract and analyze stream configurations from connection JSONs.

        Returns:
            DataFrame with stream analysis
        """
        streams = []

        for tool, benchmarks in self.benchmarks.items():
            for benchmark in benchmarks:
                if "connection" in benchmark and isinstance(benchmark["connection"], dict):
                    conn = benchmark["connection"]
                    sync_catalog = conn.get("syncCatalog", {})

                    if not isinstance(sync_catalog, dict):
                        continue

                    stream_list = sync_catalog.get("streams", [])

                    for stream_item in stream_list:
                        if not isinstance(stream_item, dict) or "stream" not in stream_item:
                            continue

                        stream_data = stream_item.get("stream", {})
                        config = stream_item.get("config", {})

                        if not isinstance(stream_data, dict) or not isinstance(config, dict):
                            continue

                        stream_info = {
                            "tool": tool,
                            "benchmark_id": benchmark["id"],
                            "connection_name": conn.get("name", ""),
                            "stream_name": stream_data.get("name", ""),
                            "namespace": stream_data.get("namespace", ""),
                            "sync_mode": config.get("syncMode", ""),
                            "destination_sync_mode": config.get("destinationSyncMode", ""),
                            "has_primary_key": bool(config.get("primaryKey", [])),
                            "has_cursor_field": bool(config.get("cursorField", [])),
                            "selected": config.get("selected", False),
                        }

                        # Extract supported sync modes
                        supported_modes = stream_data.get("supportedSyncModes", [])
                        stream_info["supported_sync_modes"] = ", ".join(supported_modes) if supported_modes else ""

                        # Check for schema properties
                        json_schema = stream_data.get("jsonSchema", {})
                        if isinstance(json_schema, dict):
                            properties = json_schema.get("properties", {})
                            stream_info["property_count"] = len(properties) if isinstance(properties, dict) else 0

                        streams.append(stream_info)

        return pd.DataFrame(streams)

    def plot_tool_comparison(self) -> None:
        """Plot comparison of tools based on benchmark count with enhanced styling."""
        summary = self.get_tool_summary()

        # Sort by benchmark count for better readability
        summary = summary.sort_values("benchmark_count", ascending=False)

        # Create figure with better dimensions
        plt.figure(figsize=(14, 7))

        # Create barplot with enhanced styling
        ax = sns.barplot(
            x="tool",
            y="benchmark_count",
            data=summary,
            palette="Blues_d",  # Use a color gradient
            alpha=0.85,  # Slightly transparent
            edgecolor="white",  # White edge for contrast
            linewidth=1.5,  # Thicker edge
        )

        # Add drop shadow effect
        for patch in ax.patches:
            patch.set_path_effects([path_effects.SimpleLineShadow(offset=(2, -2), alpha=0.3), path_effects.Normal()])

        # Add data labels with enhanced styling
        for i, v in enumerate(summary["benchmark_count"]):
            ax.text(
                i,
                v + 0.5,
                str(v),
                ha="center",
                va="bottom",
                fontweight="bold",
                color="#333333",
                fontsize=11,
                path_effects=[path_effects.withSimplePatchShadow(offset=(1, -1), shadow_rgbFace="white", alpha=0.8)],
            )

        # Add horizontal line for average
        avg = summary["benchmark_count"].mean()
        ax.axhline(y=avg, linestyle="--", color="#E74C3C", linewidth=1.5, alpha=0.7)
        ax.text(
            len(summary) - 0.5,
            avg + 0.5,
            f"Average: {avg:.1f}",
            ha="right",
            va="bottom",
            color="#E74C3C",
            fontsize=10,
            fontweight="bold",
            bbox=dict(facecolor="white", alpha=0.7, edgecolor="none", boxstyle="round,pad=0.3"),
        )

        # Enhance title and labels
        plt.title("Number of Benchmarks per Tool", fontsize=18, fontweight="bold", pad=20)
        plt.xlabel("Tool", fontsize=14)
        plt.ylabel("Benchmark Count", fontsize=14)
        plt.xticks(rotation=45, ha="right")

        # Add subtle grid lines only for y-axis
        ax.yaxis.grid(True, linestyle="--", alpha=0.4)
        ax.xaxis.grid(False)

        # Make the plot background a gradient
        ax.set_facecolor("#f8f9fa")

        plt.tight_layout()
        plt.show()

    def plot_json_complexity(self) -> None:
        """Plot JSON complexity metrics across tools with enhanced styling."""
        json_analysis = self.get_json_structure_stats()

        if json_analysis.empty:
            print("No JSON files found for analysis.")
            return

        # Prepare data for plotting
        json_stats = (
            json_analysis.groupby("tool")
            .agg({"depth": ["mean", "max", "count"], "keys": ["mean", "max"], "size": ["mean", "max"]})
            .reset_index()
        )

        json_stats.columns = ["_".join(col).strip("_") for col in json_stats.columns.values]

        # Sort by depth_mean for more intuitive display
        json_stats = json_stats.sort_values("depth_mean", ascending=False)

        # Create a more appealing figure with 3 subplots
        fig, axes = plt.subplots(1, 3, figsize=(20, 8))
        fig.suptitle("JSON Structure Analysis by Tool", fontsize=20, fontweight="bold", y=1.05)

        # Set a background color for the entire figure
        fig.patch.set_facecolor("#f9f9f9")

        # Custom color gradients for each plot
        depth_palette = sns.color_palette("Blues_d", len(json_stats))
        keys_palette = sns.color_palette("Greens_d", len(json_stats))
        size_palette = sns.color_palette("Oranges_d", len(json_stats))

        # Depth plot with enhanced styling
        sns.barplot(
            x="tool",
            y="depth_mean",
            data=json_stats,
            ax=axes[0],
            palette=depth_palette,
            edgecolor="white",
            linewidth=1.5,
        )

        # Add error bars to show min-max range
        for i, row in json_stats.iterrows():
            axes[0].errorbar(
                i,
                row["depth_mean"],
                yerr=row["depth_max"] - row["depth_mean"],
                fmt="none",
                color="#333333",
                capsize=5,
                elinewidth=1.5,
                alpha=0.7,
            )

        axes[0].set_title("JSON Depth", fontsize=16, fontweight="bold", pad=15)
        axes[0].set_xlabel("Tool", fontsize=12, fontweight="bold")
        axes[0].set_ylabel("Average Depth", fontsize=12, fontweight="bold")
        axes[0].tick_params(axis="x", rotation=45, labelsize=10)

        # Add value labels
        for i, v in enumerate(json_stats["depth_mean"]):
            axes[0].text(
                i,
                v + 0.1,
                f"{v:.1f}",
                ha="center",
                va="bottom",
                fontsize=9,
                fontweight="bold",
                bbox=dict(facecolor="white", alpha=0.7, edgecolor="none", boxstyle="round,pad=0.2"),
            )

        # Keys plot
        sns.barplot(
            x="tool", y="keys_mean", data=json_stats, ax=axes[1], palette=keys_palette, edgecolor="white", linewidth=1.5
        )

        # Add error bars to show min-max range
        for i, row in json_stats.iterrows():
            axes[1].errorbar(
                i,
                row["keys_mean"],
                yerr=row["keys_max"] - row["keys_mean"],
                fmt="none",
                color="#333333",
                capsize=5,
                elinewidth=1.5,
                alpha=0.7,
            )

        axes[1].set_title("JSON Keys", fontsize=16, fontweight="bold", pad=15)
        axes[1].set_xlabel("Tool", fontsize=12, fontweight="bold")
        axes[1].set_ylabel("Average Number of Keys", fontsize=12, fontweight="bold")
        axes[1].tick_params(axis="x", rotation=45, labelsize=10)

        # Add value labels
        for i, v in enumerate(json_stats["keys_mean"]):
            axes[1].text(
                i,
                v + 0.1,
                f"{v:.1f}",
                ha="center",
                va="bottom",
                fontsize=9,
                fontweight="bold",
                bbox=dict(facecolor="white", alpha=0.7, edgecolor="none", boxstyle="round,pad=0.2"),
            )

        # Size plot (with KB conversion for readability)
        # Convert bytes to kilobytes for better readability
        json_stats["size_mean_kb"] = json_stats["size_mean"] / 1024
        json_stats["size_max_kb"] = json_stats["size_max"] / 1024

        sns.barplot(
            x="tool",
            y="size_mean_kb",
            data=json_stats,
            ax=axes[2],
            palette=size_palette,
            edgecolor="white",
            linewidth=1.5,
        )

        # Add error bars to show min-max range
        for i, row in json_stats.iterrows():
            axes[2].errorbar(
                i,
                row["size_mean_kb"],
                yerr=row["size_max_kb"] - row["size_mean_kb"],
                fmt="none",
                color="#333333",
                capsize=5,
                elinewidth=1.5,
                alpha=0.7,
            )

        axes[2].set_title("JSON Size", fontsize=16, fontweight="bold", pad=15)
        axes[2].set_xlabel("Tool", fontsize=12, fontweight="bold")
        axes[2].set_ylabel("Average Size (KB)", fontsize=12, fontweight="bold")
        axes[2].tick_params(axis="x", rotation=45, labelsize=10)

        # Add value labels
        for i, v in enumerate(json_stats["size_mean_kb"]):
            axes[2].text(
                i,
                v + 0.1,
                f"{v:.1f}",
                ha="center",
                va="bottom",
                fontsize=9,
                fontweight="bold",
                bbox=dict(facecolor="white", alpha=0.7, edgecolor="none", boxstyle="round,pad=0.2"),
            )

        # Improve grid appearance on all subplots
        for ax in axes:
            ax.yaxis.grid(True, linestyle="--", alpha=0.3)
            ax.xaxis.grid(False)
            ax.set_facecolor("#f8f9fa")  # Light background

        plt.tight_layout()
        plt.subplots_adjust(top=0.85, wspace=0.3)  # Adjust for the suptitle
        plt.show()

    def find_benchmarks(self, search_term: str, case_sensitive: bool = False) -> pd.DataFrame:
        """
        Find benchmarks containing the search term in JSON files.

        Parameters:
            search_term (str): Term to search for
            case_sensitive (bool): Whether to perform case-sensitive search

        Returns:
            DataFrame with matching benchmarks
        """
        matches = []

        for tool, benchmarks in self.benchmarks.items():
            for benchmark in benchmarks:
                # Search in all loaded JSON data
                for key, value in benchmark.items():
                    if key in ["id", "path", "files"]:
                        continue

                    if isinstance(value, dict) or isinstance(value, list):
                        # Convert to string for searching
                        json_str = json.dumps(value)

                        if not case_sensitive:
                            json_str = json_str.lower()
                            term = search_term.lower()
                        else:
                            term = search_term

                        if term in json_str:
                            matches.append(
                                {
                                    "tool": tool,
                                    "benchmark_id": benchmark["id"],
                                    "file": f"{key}.json",
                                    "match_context": self._get_match_context(json_str, term),
                                }
                            )

        return pd.DataFrame(matches)

    def _get_match_context(self, text: str, term: str, context_size: int = 50) -> str:
        """Extract context around the search term."""
        index = text.find(term)
        if index == -1:
            return ""

        start = max(0, index - context_size)
        end = min(len(text), index + len(term) + context_size)

        # Add ellipsis if needed
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(text) else ""

        return f"{prefix}{text[start:end]}{suffix}"

    def analyze_schema_properties(self) -> pd.DataFrame:
        """
        Analyze JSON schema properties from connection configurations.

        Returns:
            DataFrame with schema property analysis
        """
        schema_properties = []

        for tool, benchmarks in self.benchmarks.items():
            for benchmark in benchmarks:
                if "connection" not in benchmark or not benchmark["connection"]:
                    continue

                conn = benchmark["connection"]
                sync_catalog = conn.get("syncCatalog", {})

                if not isinstance(sync_catalog, dict):
                    continue

                streams = sync_catalog.get("streams", [])

                for stream in streams:
                    if not isinstance(stream, dict) or "stream" not in stream:
                        continue

                    stream_data = stream["stream"]
                    if not isinstance(stream_data, dict):
                        continue

                    # Get JSON schema
                    json_schema = stream_data.get("jsonSchema", {})
                    if not isinstance(json_schema, dict):
                        continue

                    # Get properties
                    properties = json_schema.get("properties", {})
                    if not isinstance(properties, dict):
                        continue

                    stream_name = stream_data.get("name", "unknown")

                    # Analyze each property
                    for prop_name, prop_details in properties.items():
                        if not isinstance(prop_details, dict):
                            continue

                        # Convert list type to string to ensure hashable values
                        prop_type = prop_details.get("type", "unknown")
                        if isinstance(prop_type, list):
                            prop_type = ", ".join(str(t) for t in prop_type)

                        property_data = {
                            "tool": tool,
                            "benchmark_id": benchmark["id"],
                            "stream_name": stream_name,
                            "property_name": prop_name,
                            "property_type": prop_type,  # Use the converted value
                            "has_airbyte_type": "airbyte_type" in prop_details,
                        }

                        if "airbyte_type" in prop_details:
                            property_data["airbyte_type"] = prop_details["airbyte_type"]

                        schema_properties.append(property_data)

        return pd.DataFrame(schema_properties)

    def generate_report(self, output_file: str = "benchmark_analysis.html") -> None:
        """
        Generate an HTML report with comprehensive EDA findings.

        Parameters:
            output_file (str): Output HTML file path
        """
        import base64
        from io import BytesIO

        # Helper function to convert plot to base64
        def fig_to_base64(fig):
            buf = BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight")
            buf.seek(0)
            return base64.b64encode(buf.getvalue()).decode("utf-8")

        # Start building HTML
        html = []
        html.append("<!DOCTYPE html>")
        html.append("<html><head>")
        html.append("<title>Benchmark Data Analysis Report</title>")
        html.append("<style>")
        html.append("body { font-family: Arial, sans-serif; margin: 20px; }")
        html.append("h1, h2, h3 { color: #333; }")
        html.append("table { border-collapse: collapse; width: 100%; margin-bottom: 20px; }")
        html.append("th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }")
        html.append("th { background-color: #f2f2f2; }")
        html.append("tr:nth-child(even) { background-color: #f9f9f9; }")
        html.append(".section { margin-bottom: 30px; }")
        html.append(".plot { text-align: center; margin: 20px 0; }")
        html.append("</style>")
        html.append("</head><body>")

        # Title
        html.append("<h1>Spider2-V Benchmark Data Analysis Report</h1>")

        # Summary section
        html.append('<div class="section">')
        html.append("<h2>Summary</h2>")

        summary = self.get_summary()
        html.append(f"<p>Total Tools: {summary['total_tools']}</p>")
        html.append(f"<p>Total Benchmarks: {summary['total_benchmarks']}</p>")
        html.append(f"<p>Total JSON Configs: {summary['json_configs']}</p>")
        html.append(f"<p>Total Connection Configs: {summary['connection_configs']}</p>")

        # Tool summary table
        html.append("<h3>Tool Summary</h3>")
        tool_summary = self.get_tool_summary()
        html.append(tool_summary.to_html(index=False))

        # Tool comparison plot
        html.append('<div class="plot">')
        html.append("<h3>Benchmark Count by Tool</h3>")

        plt.figure(figsize=(12, 6))
        ax = sns.barplot(x="tool", y="benchmark_count", data=tool_summary)
        for i, v in enumerate(tool_summary["benchmark_count"]):
            ax.text(i, v + 0.1, str(v), ha="center")
        plt.title("Number of Benchmarks per Tool")
        plt.xlabel("Tool")
        plt.ylabel("Benchmark Count")
        plt.xticks(rotation=45)
        plt.tight_layout()

        plot_data = fig_to_base64(plt.gcf())
        html.append(f'<img src="data:image/png;base64,{plot_data}" alt="Benchmark Count by Tool">')
        html.append("</div>")
        html.append("</div>")  # End summary section

        # File type analysis
        html.append('<div class="section">')
        html.append("<h2>File Type Analysis</h2>")

        # File type distribution
        html.append('<div class="plot">')
        html.append("<h3>Distribution of File Types</h3>")

        # Create plot for file type distribution
        file_type_data = []
        for tool, tool_benchmarks in self.benchmarks.items():
            file_types = self._count_file_types(tool)
            for ext, count in file_types.items():
                file_type_data.append({"tool": tool, "file_type": ext, "count": count})

        df_file_types = pd.DataFrame(file_type_data)

        if not df_file_types.empty:
            # Get top file types
            top_types = df_file_types.groupby("file_type")["count"].sum().nlargest(8).index.tolist()
            df_filtered = df_file_types[df_file_types["file_type"].isin(top_types)]

            plt.figure(figsize=(14, 8))
            sns.barplot(x="file_type", y="count", hue="tool", data=df_filtered)
            plt.title("Distribution of Top File Types by Tool")
            plt.xlabel("File Type")
            plt.ylabel("Count")
            plt.xticks(rotation=45)
            plt.tight_layout()

            plot_data = fig_to_base64(plt.gcf())
            html.append(f'<img src="data:image/png;base64,{plot_data}" alt="File Type Distribution">')

        html.append("</div>")
        html.append("</div>")  # End file type section

        # JSON Structure Analysis
        html.append('<div class="section">')
        html.append("<h2>JSON Structure Analysis</h2>")

        json_analysis = self.get_json_structure_stats()
        if not json_analysis.empty:
            # JSON complexity table
            html.append("<h3>JSON File Complexity</h3>")
            json_complexity = (
                json_analysis.groupby("tool")
                .agg({"depth": ["mean", "max"], "keys": ["mean", "max"], "size": ["mean", "max"]})
                .reset_index()
            )

            json_complexity.columns = ["_".join(col).strip("_") for col in json_complexity.columns.values]
            html.append(json_complexity.to_html(index=False))

            # JSON complexity plot
            html.append('<div class="plot">')
            html.append("<h3>JSON Complexity by Tool</h3>")

            plt.figure(figsize=(14, 6))
            sns.barplot(x="tool", y="depth_mean", data=json_complexity)
            plt.title("Average JSON Depth by Tool")
            plt.xlabel("Tool")
            plt.ylabel("Average Depth")
            plt.xticks(rotation=45)
            plt.tight_layout()

            plot_data = fig_to_base64(plt.gcf())
            html.append(f'<img src="data:image/png;base64,{plot_data}" alt="JSON Complexity">')
            html.append("</div>")

        html.append("</div>")  # End JSON section

        # Connection Analysis
        html.append('<div class="section">')
        html.append("<h2>Connection Configuration Analysis</h2>")

        connections = self.analyze_connections()
        if not connections.empty:
            html.append("<h3>Connection Summary</h3>")

            # Count connections by tool
            conn_by_tool = connections.groupby("tool").size().reset_index(name="connection_count")
            html.append(conn_by_tool.to_html(index=False))

            # Add percentage of benchmarks with connections
            html.append("<h4>Connection Coverage Analysis</h4>")
            tool_summary = self.get_tool_summary()
            conn_coverage = []

            for tool in self.tools:
                if tool in conn_by_tool["tool"].values:
                    conn_count = conn_by_tool.loc[conn_by_tool["tool"] == tool, "connection_count"].iloc[0]
                else:
                    conn_count = 0

                benchmark_count = (
                    tool_summary.loc[tool_summary["tool"] == tool, "benchmark_count"].iloc[0]
                    if tool in tool_summary["tool"].values
                    else 0
                )
                coverage = (conn_count / benchmark_count * 100) if benchmark_count > 0 else 0

                conn_coverage.append(
                    {
                        "tool": tool,
                        "connection_count": conn_count,
                        "benchmark_count": benchmark_count,
                        "coverage_percent": round(coverage, 2),
                    }
                )

            conn_coverage_df = pd.DataFrame(conn_coverage)
            html.append(conn_coverage_df.to_html(index=False))

            # Create an enhanced connection visualization with coverage percentage
            html.append('<div class="plot">')
            html.append("<h3>Connection Coverage by Tool</h3>")

            plt.figure(figsize=(14, 8))

            # Create two subplots side by side
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(20, 8))
            fig.suptitle("Connection Analysis by Tool", fontsize=16, fontweight="bold")

            # Plot 1: Raw connection counts
            sns.barplot(
                x="tool",
                y="connection_count",
                data=conn_coverage_df,
                ax=ax1,
                palette="Blues_d",
                edgecolor="white",
                linewidth=1.5,
            )
            ax1.set_title("Number of Connections per Tool")
            ax1.set_xlabel("Tool")
            ax1.set_ylabel("Connection Count")
            ax1.tick_params(axis="x", rotation=45)

            # Add data labels
            for i, v in enumerate(conn_coverage_df["connection_count"]):
                ax1.text(i, v + 0.1, str(v), ha="center")

            # Plot 2: Coverage percentage
            sns.barplot(
                x="tool",
                y="coverage_percent",
                data=conn_coverage_df,
                ax=ax2,
                palette="Greens_d",
                edgecolor="white",
                linewidth=1.5,
            )
            ax2.set_title("Connection Coverage (% of Benchmarks with Connections)")
            ax2.set_xlabel("Tool")
            ax2.set_ylabel("Coverage Percentage")
            ax2.tick_params(axis="x", rotation=45)
            ax2.set_ylim(0, 100)  # Set y-axis to be from 0-100%

            # Add data labels with percentage
            for i, v in enumerate(conn_coverage_df["coverage_percent"]):
                ax2.text(i, v + 1, f"{v}%", ha="center")

            plt.tight_layout()
            plt.subplots_adjust(top=0.9)  # Adjust for the suptitle

            plot_data = fig_to_base64(plt.gcf())
            html.append(f'<img src="data:image/png;base64,{plot_data}" alt="Connection Coverage Analysis">')
            html.append("</div>")

            # Stream analysis
            streams = self.analyze_streams()
            if not streams.empty:
                html.append("<h3>Stream Configuration Summary</h3>")

                # Count streams by tool
                stream_by_tool = streams.groupby("tool").size().reset_index(name="stream_count")
                html.append(stream_by_tool.to_html(index=False))

        html.append("</div>")  # End connection section

        # Schema Property Analysis
        html.append('<div class="section">')
        html.append("<h2>Schema Property Analysis</h2>")

        schema_props = self.analyze_schema_properties()
        if not schema_props.empty:
            html.append("<h3>Property Type Distribution</h3>")

            # Count property types
            prop_types = schema_props.groupby("property_type").size().reset_index(name="count")
            html.append(prop_types.to_html(index=False))

            # Property type visualization
            html.append('<div class="plot">')
            html.append("<h3>Property Types</h3>")

            plt.figure(figsize=(10, 6))
            sns.barplot(x="property_type", y="count", data=prop_types)
            plt.title("Distribution of Property Types")
            plt.xlabel("Property Type")
            plt.ylabel("Count")
            plt.xticks(rotation=45)
            plt.tight_layout()

            plot_data = fig_to_base64(plt.gcf())
            html.append(f'<img src="data:image/png;base64,{plot_data}" alt="Property Types">')
            html.append("</div>")

        html.append("</div>")  # End schema property section

        # Enhanced File Type Insights
        html.append('<div class="section">')
        html.append("<h2>Enhanced File Type Insights</h2>")

        # Add file type patterns
        file_patterns = self.get_file_type_patterns()
        html.append("<h3>File Type Patterns by Tool</h3>")
        html.append(file_patterns.to_html(index=False))

        # File type heat map
        html.append('<div class="plot">')
        html.append("<h3>File Type Distribution Heatmap</h3>")

        # Create heatmap visualization
        file_data = self.analyze_file_types()
        pivot = file_data.pivot_table(index="tool", columns="file_type", values="percentage", fill_value=0)

        # Filter to top file types
        top_types = file_data.groupby("file_type")["count"].sum().nlargest(8).index.tolist()
        pivot_filtered = pivot[top_types]

        plt.figure(figsize=(14, 8))
        sns.heatmap(pivot_filtered, annot=True, fmt=".1f", cmap="YlGnBu", linewidths=0.5)
        plt.title("File Type Distribution by Tool (%)")
        plt.tight_layout()

        plot_data = fig_to_base64(plt.gcf())
        html.append(f'<img src="data:image/png;base64,{plot_data}" alt="File Type Heatmap">')
        html.append("</div>")

        html.append("</div>")  # End enhanced file type section

        html.append("</body></html>")

        # Write to file
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("\n".join(html))

        print(f"Report generated: {output_file}")

    def analyze_benchmark_names(self) -> pd.DataFrame:
        """
        Analyze benchmark names from connection configurations.

        Returns:
            DataFrame with benchmark name analysis
        """
        name_patterns = []

        for tool, benchmarks in self.benchmarks.items():
            for benchmark in benchmarks:
                # Check for connection JSON
                if "connection" in benchmark and isinstance(benchmark["connection"], dict):
                    conn = benchmark["connection"]
                    name = conn.get("name", "")

                    if name:
                        # Extract patterns from name
                        parts = name.split("→") if "→" in name else [name]
                        source = parts[0].strip() if len(parts) > 0 else ""
                        destination = parts[1].strip() if len(parts) > 1 else ""

                        name_patterns.append(
                            {
                                "tool": tool,
                                "benchmark_id": benchmark["id"],
                                "full_name": name,
                                "source": source,
                                "destination": destination,
                                "has_arrow": "→" in name,
                            }
                        )

        return pd.DataFrame(name_patterns)

    def export_data(self, output_dir: str = "benchmark_analysis_exports") -> None:
        """
        Export all analysis data to CSV files.

        Parameters:
            output_dir (str): Directory to save CSV files
        """
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)

        # Get all analysis data
        tool_summary = self.get_tool_summary()
        json_structure = self.get_json_structure_stats()
        connections = self.analyze_connections()
        benchmark_content = self.analyze_benchmark_content()
        streams = self.analyze_streams()
        schema_properties = self.analyze_schema_properties()
        file_types = self.analyze_file_types()
        benchmark_names = self.analyze_benchmark_names()
        file_patterns = self.get_file_type_patterns()
        file_signatures = self.get_file_signature_by_tool()
        file_insights = self.analyze_file_type_insights()

        # Export to CSV
        tool_summary.to_csv(f"{output_dir}/tool_summary.csv", index=False)
        json_structure.to_csv(f"{output_dir}/json_structure.csv", index=False)
        connections.to_csv(f"{output_dir}/connections.csv", index=False)
        benchmark_content.to_csv(f"{output_dir}/benchmark_content.csv", index=False)
        streams.to_csv(f"{output_dir}/streams.csv", index=False)
        schema_properties.to_csv(f"{output_dir}/schema_properties.csv", index=False)
        file_types.to_csv(f"{output_dir}/file_types.csv", index=False)
        benchmark_names.to_csv(f"{output_dir}/benchmark_names.csv", index=False)
        file_patterns.to_csv(f"{output_dir}/file_patterns.csv", index=False)
        file_signatures.to_csv(f"{output_dir}/file_signatures.csv", index=False)
        file_insights.to_csv(f"{output_dir}/file_insights.csv", index=False)

        print(f"All data exported to {output_dir}/")
