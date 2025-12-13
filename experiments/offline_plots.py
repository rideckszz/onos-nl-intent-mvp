"""
Offline plotting for ONOS NL-intent mediator experiments.

FINAL REVISION:
- Enforces strict topology order: Linear (3,6,9) -> Tree (d2f2, d2f3).
- Clarifies Scaling Trend labels (Total Latency: Planning + Execution).
- Maintains split latency views and clean aesthetics.
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from plottable import Table, ColumnDefinition

# -----------------------------
# Configuration & Mappings
# -----------------------------
OP_DISPLAY_MAP = {
    "connect_hosts": "Host Connection",
    "list_intents": "List Intents",
    "delete_all_intents": "Delete All",
    "delete_intents_between_hosts": "Specific Deletion",
    "host_to_host": "Host Connection",
    "create_intent": "Host Connection",
}

TOPO_DISPLAY_MAP = {
    "linear_3": "Linear (3 Hosts)",
    "linear_6": "Linear (6 Hosts)",
    "linear_9": "Linear (9 Hosts)",
    "tree_d2_f2": "Tree (Depth 2, Fanout 2)",
    "tree_d2_f3": "Tree (Depth 2, Fanout 3)",
}

# Strict Order: Linear 3->6->9, then Tree 2->3
TOPO_SORT_PRIORITY = [
    "linear_3",
    "linear_6",
    "linear_9",
    "tree_d2_f2",
    "tree_d2_f3"
]

ERROR_DISPLAY_MAP = {
    "Verification failed for delete_intents_between_hos": "Verification Error",
    "delete_all_intents left 1 intents for appId": "Incomplete Deletion",
    "JSONDecodeError": "JSON Parse Error",
    "timed out": "Timeout",
}

def get_op_display_name(raw_op: str) -> str:
    norm_op = str(raw_op).strip().lower()
    if norm_op in OP_DISPLAY_MAP: return OP_DISPLAY_MAP[norm_op]
    for key, val in OP_DISPLAY_MAP.items():
        if key in norm_op: return val
    return raw_op.replace("_", " ").title()

def get_topo_display_name(raw_topo: str) -> str:
    raw_topo_str = str(raw_topo).strip()
    if raw_topo_str in TOPO_DISPLAY_MAP: return TOPO_DISPLAY_MAP[raw_topo_str]
    return raw_topo_str.replace("_", " ").title()

def get_error_display_name(raw_err: str) -> str:
    for key, val in ERROR_DISPLAY_MAP.items():
        if key in raw_err: return val
    e = re.sub(r"Traceback.*", "", str(raw_err), flags=re.DOTALL)
    e = re.sub(r"0x[0-9a-fA-F]+", "", e)
    return (re.sub(r"\s+", " ", e).strip())[:40]

# -----------------------------
# Style helpers
# -----------------------------
def apply_science_style() -> None:
    try:
        import scienceplots  # noqa: F401
        plt.style.use(["science", "no-latex"])
    except Exception:
        plt.rcParams.update({
            "axes.grid": True, "grid.alpha": 0.3,
            "axes.spines.top": False, "axes.spines.right": False,
        })
    plt.rcParams.update({
        "figure.dpi": 150, "savefig.dpi": 300,
        "font.size": 12, "axes.titlesize": 14, "axes.labelsize": 12,
        "xtick.labelsize": 11, "ytick.labelsize": 11,
        "text.usetex": False, "font.family": "sans-serif",
    })

def save_fig(path_base: str) -> None:
    plt.tight_layout()
    plt.savefig(path_base + ".png", bbox_inches="tight")
    plt.savefig(path_base + ".pdf", bbox_inches="tight")
    plt.close()

def save_plottable_table(df: pd.DataFrame, title: str, out_path_base: str):
    if df.empty: return
    df_formatted = df.copy()
    for col in df_formatted.select_dtypes(include=['float']).columns:
        df_formatted[col] = df_formatted[col].apply(lambda x: f"{x:.2f}")

    fig, ax = plt.subplots(figsize=(8, len(df) * 0.5 + 2.0))
    tab = Table(
        df_formatted,
        textprops={"ha": "left", "fontsize": 12},
        column_definitions=[ColumnDefinition(name=col, textprops={"ha": "left"}) for col in df.columns]
    )
    plt.title(title, fontsize=14, pad=20)
    ax.axis('off')
    plt.savefig(out_path_base + ".png", bbox_inches='tight', dpi=300)
    plt.savefig(out_path_base + ".pdf", bbox_inches='tight')
    plt.close()

# -----------------------------
# CSV Schema & Data
# -----------------------------
@dataclass
class CsvSchema:
    topo: str
    n_hosts: Optional[str]
    op: str
    success: str
    error: Optional[str]
    plan_ms: Optional[str]
    ctrl_ms: Optional[str]
    verify_ms: Optional[str]
    total_ms: Optional[str]
    baseline_total_ms: Optional[str]

def infer_schema(df: pd.DataFrame) -> CsvSchema:
    cols = list(df.columns)
    topo = "topology_label" if "topology_label" in cols else "topo"
    n_hosts = "hosts_count" if "hosts_count" in cols else "n_hosts"
    op = "operation"
    success = "ok" if "ok" in cols else "success"
    error = "error"
    total_ms = "total_ms"
    plan_ms = "llm_ms"
    ctrl_ms = "onos_ms"
    verify_ms = "verify_ms"
    baseline_total_ms = "baseline_total_ms" if "baseline_total_ms" in cols else None

    return CsvSchema(topo, n_hosts, op, success, error, plan_ms, ctrl_ms, verify_ms, total_ms, baseline_total_ms)

def coerce_bool_series(s: pd.Series) -> pd.Series:
    if s.dtype == bool: return s
    def _conv(x): return str(x).lower().strip() in {"true", "1", "yes", "ok", "success", "t"}
    return s.apply(_conv)

def get_sorted_topologies(df: pd.DataFrame, schema: CsvSchema) -> List[str]:
    """
    Sorts topologies based on the strict TOPO_SORT_PRIORITY list.
    Any topology not in the list is appended at the end.
    """
    topos = df[schema.topo].unique()

    def sort_key(t_name):
        t_str = str(t_name).strip()
        if t_str in TOPO_SORT_PRIORITY:
            return TOPO_SORT_PRIORITY.index(t_str)
        return 999  # Put unknown topos at the end

    return sorted(topos, key=sort_key)

def summarize_errors(df: pd.DataFrame, schema: CsvSchema) -> pd.DataFrame:
    if not schema.error: return pd.DataFrame()
    df2 = df.copy()
    df2["_ok"] = coerce_bool_series(df2[schema.success])
    failures = df2[~df2["_ok"]][schema.error].dropna().astype(str)

    clean_failures = failures.apply(get_error_display_name)
    counts = clean_failures.value_counts().reset_index()
    counts.columns = ["Error Type", "Count"]
    return counts

# -----------------------------
# PLOTTING FUNCTIONS
# -----------------------------
FIG_SIZE = (6, 4)

def plot_success_rates(df: pd.DataFrame, schema: CsvSchema, outdir: str):
    apply_science_style()
    df2 = df.copy()
    df2["_ok"] = coerce_bool_series(df2[schema.success])

    # By Topology
    topo_rates = df2.groupby(schema.topo)["_ok"].mean()
    sorted_topos = get_sorted_topologies(df, schema)
    topo_rates = topo_rates.reindex(sorted_topos)
    topo_rates.index = topo_rates.index.map(get_topo_display_name)

    plt.figure(figsize=FIG_SIZE)
    topo_rates.plot(kind="bar", color="#4C72B0", edgecolor="black", width=0.6, alpha=0.9)
    plt.ylabel("Success Rate")
    plt.xlabel("")
    plt.title("Success Rate by Topology")
    plt.ylim(0, 1.05)
    plt.xticks(rotation=25, ha='right')
    save_fig(os.path.join(outdir, "success_rate_topology"))

    # By Operation
    op_rates = df2.groupby(schema.op)["_ok"].mean().sort_values(ascending=False)
    op_rates.index = op_rates.index.map(get_op_display_name)

    plt.figure(figsize=FIG_SIZE)
    op_rates.plot(kind="bar", color="#55A868", edgecolor="black", width=0.6, alpha=0.9)
    plt.ylabel("Success Rate")
    plt.xlabel("")
    plt.title("Success Rate by Operation")
    plt.ylim(0, 1.05)
    plt.xticks(rotation=25, ha='right')
    save_fig(os.path.join(outdir, "success_rate_operation"))

def plot_error_distribution(df: pd.DataFrame, schema: CsvSchema, outdir: str):
    if not schema.error: return
    apply_science_style()
    df2 = df.copy()
    df2["_ok"] = coerce_bool_series(df2[schema.success])

    failures = df2[~df2["_ok"]][schema.error].dropna().astype(str)
    if failures.empty: return

    clean_failures = failures.apply(get_error_display_name)
    counts = clean_failures.value_counts().head(8)

    plt.figure(figsize=FIG_SIZE)
    counts.sort_values().plot(kind="barh", color="#C44E52", edgecolor="black", alpha=0.8)
    plt.xlabel("Count")
    plt.title("Distribution of Error Types")
    plt.grid(axis='x')
    plt.tight_layout()
    save_fig(os.path.join(outdir, "error_distribution"))

def plot_latency_split(df: pd.DataFrame, schema: CsvSchema, outdir: str):
    if not schema.plan_ms or not schema.ctrl_ms: return
    apply_science_style()
    sorted_topos = get_sorted_topologies(df, schema)
    display_labels = [get_topo_display_name(t) for t in sorted_topos]

    # --- Plot 1: LLM Latency ---
    llm_means = []
    for t in sorted_topos:
        val = pd.to_numeric(df[df[schema.topo] == t][schema.plan_ms], errors='coerce').mean()
        llm_means.append(val if not pd.isna(val) else 0)

    plt.figure(figsize=FIG_SIZE)
    plt.bar(display_labels, llm_means, color="#CCB974", edgecolor="black", width=0.6, alpha=0.9)
    plt.ylabel("Mean Latency (ms)")
    plt.title("Planning Latency (LLM Generation)")
    plt.xticks(rotation=25, ha='right')
    save_fig(os.path.join(outdir, "latency_planning_llm"))

    # --- Plot 2: System Execution (Controller + Verify) ---
    ctrl_means = []
    verify_means = []
    for t in sorted_topos:
        c_val = pd.to_numeric(df[df[schema.topo] == t][schema.ctrl_ms], errors='coerce').mean()
        v_val = pd.to_numeric(df[df[schema.topo] == t][schema.verify_ms], errors='coerce').mean()
        ctrl_means.append(c_val if not pd.isna(c_val) else 0)
        verify_means.append(v_val if not pd.isna(v_val) else 0)

    plt.figure(figsize=FIG_SIZE)
    bottom = np.zeros(len(sorted_topos))
    plt.bar(display_labels, ctrl_means, bottom=bottom, label="Controller", color="#64B5CD", edgecolor="black", width=0.6, alpha=0.9)
    bottom += np.array(ctrl_means)
    plt.bar(display_labels, verify_means, bottom=bottom, label="Verification", color="#C44E52", edgecolor="black", width=0.6, alpha=0.9)

    plt.ylabel("Mean Latency (ms)")
    plt.title("Execution Latency (Controller & Verification)")
    plt.legend(frameon=True, loc='upper right')
    plt.xticks(rotation=25, ha='right')
    save_fig(os.path.join(outdir, "latency_execution_system"))

def plot_scaling_trend_clean(df: pd.DataFrame, schema: CsvSchema, outdir: str):
    """
    Plots a clean trend line for Linear topologies.
    """
    if not schema.total_ms or not schema.topo: return

    df2 = df[df[schema.topo].str.contains("linear", case=False, na=False)].copy()
    if df2.empty: return

    if schema.n_hosts and schema.n_hosts in df2.columns:
         df2["_hosts"] = pd.to_numeric(df2[schema.n_hosts], errors='coerce')
    else: return

    df2 = df2.dropna(subset=["_hosts"])
    df2["_total"] = pd.to_numeric(df2[schema.total_ms], errors='coerce')

    # Calculate Mean
    scaling = df2.groupby("_hosts")["_total"].mean().sort_index()
    if scaling.empty: return

    apply_science_style()
    plt.figure(figsize=FIG_SIZE)

    plt.plot(scaling.index, scaling.values, marker='o', linestyle='-', linewidth=2, markersize=8, color="#4C72B0")

    # Updated explicit label
    plt.ylabel("Total Latency (ms)\n(Planning + Execution)")
    plt.xlabel("Number of Hosts")
    plt.title("Latency Scaling (Linear Topologies)")
    plt.xticks(scaling.index)
    plt.grid(True, linestyle='--')
    save_fig(os.path.join(outdir, "latency_scaling_trend"))

def plot_baseline_comparison(df: pd.DataFrame, schema: CsvSchema, outdir: str):
    if not schema.baseline_total_ms or not schema.total_ms: return
    apply_science_style()
    df2 = df.copy()
    df2["_mediator"] = pd.to_numeric(df2[schema.total_ms], errors='coerce')
    df2["_baseline"] = pd.to_numeric(df2[schema.baseline_total_ms], errors='coerce')
    df2 = df2.dropna(subset=["_mediator", "_baseline"])
    if df2.empty: return

    means = [df2["_mediator"].mean(), df2["_baseline"].mean()]
    stds = [df2["_mediator"].std(), df2["_baseline"].std()]
    labels = ["LLM Mediator", "Manual Baseline"]

    plt.figure(figsize=FIG_SIZE)
    plt.bar(labels, means, yerr=stds, capsize=5, color=["#A0A0A0", "#D0D0D0"], edgecolor="black", width=0.5)
    plt.ylabel("Mean Total Latency (ms)")
    plt.title("Mediator vs Baseline Performance")
    save_fig(os.path.join(outdir, "baseline_comparison_bar"))

# -----------------------------
# Main
# -----------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--metrics-json", default=None)
    parser.add_argument("--outdir", required=True)
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    os.makedirs(os.path.join(args.outdir, "tables"), exist_ok=True)

    print(f"Reading {args.csv}...")
    df = pd.read_csv(args.csv)
    schema = infer_schema(df)

    # 1. TABLE
    err_tab = summarize_errors(df, schema)
    if not err_tab.empty:
        save_plottable_table(err_tab, "Error Type Distribution", os.path.join(args.outdir, "tables", "table_errors"))

    # 2. PLOTS
    print("Generating Plots...")
    plot_success_rates(df, schema, args.outdir)
    plot_error_distribution(df, schema, args.outdir)
    plot_latency_split(df, schema, args.outdir)
    plot_scaling_trend_clean(df, schema, args.outdir)
    plot_baseline_comparison(df, schema, args.outdir)

    print(f"Done! Check {args.outdir}")

if __name__ == "__main__":
    main()