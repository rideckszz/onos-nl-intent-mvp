"""
Offline plotting + summary tables for ONOS NL-intent mediator experiments.

UPDATED VERSION:
- Generates publication-ready TABLE IMAGES using Plotly (instead of raw CSVs).
- Adds Scaling Trend, Error Distribution, and Complete Latency Breakdown plots.
- Enforces uniform style and dimensions across all outputs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import plotly.graph_objects as go

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

METRIC_DISPLAY_MAP = {
    "intentSubmitted": "Submitted",
    "intentInstalled": "Installed",
    "intentFailed": "Failed",
    "intentWithdrawRequested": "Withdraw Req.",
    "intentWithdrawn": "Withdrawn",
    "intentPurged": "Purged"
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

def get_metric_display_name(raw_metric: str) -> str:
    base = raw_metric.replace("Rate", "").replace("Timestamp", "")
    return METRIC_DISPLAY_MAP.get(base, base)

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
        "font.size": 11, "axes.titlesize": 12, "axes.labelsize": 11,
        "xtick.labelsize": 10, "ytick.labelsize": 10,
        "text.usetex": False, "font.family": "sans-serif",
    })

def save_fig(path_base: str) -> None:
    plt.tight_layout()
    plt.savefig(path_base + ".png", bbox_inches="tight")
    plt.savefig(path_base + ".pdf", bbox_inches="tight")
    plt.close()

def save_plotly_table(df: pd.DataFrame, title: str, out_path_base: str):
    """
    Renders a pandas DataFrame as a static Plotly table image matching the style.
    """
    if df.empty: return

    # Format floats to 2 decimal places for cleaner tables
    df_formatted = df.round(2)

    fig = go.Figure(data=[go.Table(
        header=dict(
            values=list(df_formatted.columns),
            fill_color='#4C72B0', # Match standard blue color
            align='left',
            font=dict(color='white', size=12, family="Arial")
        ),
        cells=dict(
            values=[df_formatted[k].tolist() for k in df_formatted.columns],
            fill_color='#F0F2F6', # Light grey background
            align='left',
            font=dict(color='black', size=11, family="Arial"),
            height=30
        )
    )])

    fig.update_layout(
        title_text=title,
        title_x=0.5,
        margin=dict(l=20, r=20, t=40, b=20),
        width=800,
        height=min(300 + len(df) * 30, 800) # Dynamic height
    )

    # Save as static image using Kaleido
    try:
        fig.write_image(out_path_base + ".png", scale=2)
        fig.write_image(out_path_base + ".pdf")
    except Exception as e:
        print(f"Error saving Plotly table (is kaleido installed?): {e}")

# -----------------------------
# CSV schema inference
# -----------------------------
@dataclass
class CsvSchema:
    topo: str
    n_hosts: Optional[str]
    instruction: Optional[str]
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
    def _find(candidates):
        for c in candidates:
            for existing in cols:
                if existing.lower() == c.lower(): return existing
            for existing in cols:
                if c.lower() in existing.lower(): return existing
        return None

    topo = _find(["topology", "topo", "topo_label"]) or "topology"
    op = _find(["operation", "op", "planner_operation"]) or "operation"
    success = _find(["success", "ok", "is_success", "status_ok"]) or "success"
    n_hosts = _find(["n_hosts", "num_hosts", "nhosts", "host_count"])
    instruction = _find(["instruction", "prompt", "text", "command"])
    error = _find(["error", "error_message", "exception", "failure_reason"])

    plan_ms = _find(["planning_ms", "planner_ms", "plan_time", "planning_time", "llm_time"])
    ctrl_ms = _find(["controller_ms", "onos_ms", "ctrl_time", "controller_time"])
    verify_ms = _find(["verification_ms", "verify_ms", "verify_time"])
    total_ms = _find(["total_ms", "total_time", "end_to_end"])
    baseline_total_ms = _find(["baseline_total_ms", "manual_total_ms", "baseline_ms"])

    missing = [n for n, c in [("topo", topo), ("op", op), ("success", success)] if c not in df.columns]
    if missing: raise ValueError(f"Missing required columns: {missing}")

    return CsvSchema(topo, n_hosts, instruction, op, success, error, plan_ms, ctrl_ms, verify_ms, total_ms, baseline_total_ms)

def coerce_bool_series(s: pd.Series) -> pd.Series:
    if s.dtype == bool: return s
    def _conv(x): return str(x).lower().strip() in {"true", "1", "yes", "ok", "success", "t"}
    return s.apply(_conv)

# -----------------------------
# Summary Functions
# -----------------------------
def get_sorted_topologies(df: pd.DataFrame, schema: CsvSchema) -> List[str]:
    topos = df[schema.topo].unique()
    if schema.n_hosts and schema.n_hosts in df.columns:
        mapping = {}
        for t in topos:
            subset = df[df[schema.topo] == t]
            try: n = float(subset[schema.n_hosts].max())
            except: n = 0
            mapping[t] = n
        return sorted(topos, key=lambda x: (mapping.get(x, 0), x))
    return sorted(topos)

def summarize_success(df: pd.DataFrame, schema: CsvSchema) -> Tuple[pd.DataFrame, pd.DataFrame]:
    df2 = df.copy()
    df2["_ok"] = coerce_bool_series(df2[schema.success])

    by_topo = df2.groupby(schema.topo)["_ok"].agg(["count", "sum", "mean"]).reset_index()
    by_topo.columns = ["Topology", "Count", "Successes", "Success Rate"]
    by_topo["Topology"] = by_topo["Topology"].apply(get_topo_display_name)
    by_topo = by_topo.sort_values("Success Rate", ascending=False)

    by_op = df2.groupby(schema.op)["_ok"].agg(["count", "sum", "mean"]).reset_index()
    by_op.columns = ["Operation", "Count", "Successes", "Success Rate"]
    by_op["Operation"] = by_op["Operation"].apply(get_op_display_name)
    by_op = by_op.sort_values("Success Rate", ascending=False)

    return by_topo, by_op

def summarize_errors(df: pd.DataFrame, schema: CsvSchema) -> pd.DataFrame:
    if not schema.error: return pd.DataFrame()
    df2 = df.copy()
    df2["_ok"] = coerce_bool_series(df2[schema.success])
    failures = df2[~df2["_ok"]][schema.error].dropna().astype(str)
    def clean_err(e):
        e = re.sub(r"Traceback.*", "", e, flags=re.DOTALL)
        return (re.sub(r"\s+", " ", e).strip())[:80]
    counts = failures.apply(clean_err).value_counts().reset_index()
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

    # 1. Topology
    topo_rates = df2.groupby(schema.topo)["_ok"].mean()
    sorted_topos = get_sorted_topologies(df, schema)
    topo_rates = topo_rates.reindex(sorted_topos)
    topo_rates.index = topo_rates.index.map(get_topo_display_name)

    plt.figure(figsize=FIG_SIZE)
    topo_rates.plot(kind="bar", color="#4C72B0", edgecolor="black", width=0.6, alpha=0.9)
    plt.ylabel("Success Rate")
    plt.xlabel("Topology")
    plt.title("Success Rate by Topology")
    plt.ylim(0, 1.05)
    plt.xticks(rotation=25, ha='right')
    save_fig(os.path.join(outdir, "success_rate_topology"))

    # 2. Operation
    op_rates = df2.groupby(schema.op)["_ok"].mean().sort_values(ascending=False)
    op_rates.index = op_rates.index.map(get_op_display_name)

    plt.figure(figsize=FIG_SIZE)
    op_rates.plot(kind="bar", color="#55A868", edgecolor="black", width=0.6, alpha=0.9)
    plt.ylabel("Success Rate")
    plt.xlabel("Operation")
    plt.title("Success Rate by Operation")
    plt.ylim(0, 1.05)
    plt.xticks(rotation=25, ha='right')
    save_fig(os.path.join(outdir, "success_rate_operation"))

def plot_latency_bars(df: pd.DataFrame, schema: CsvSchema, outdir: str):
    if not schema.total_ms: return
    apply_science_style()
    df2 = df.copy()
    df2["_total"] = pd.to_numeric(df2[schema.total_ms], errors='coerce')
    df2 = df2.dropna(subset=["_total"])

    # 1. By Topology
    sorted_topos = get_sorted_topologies(df, schema)
    agg_topo = df2.groupby(schema.topo)["_total"].agg(['mean', 'std']).reindex(sorted_topos)
    agg_topo.index = agg_topo.index.map(get_topo_display_name)

    plt.figure(figsize=FIG_SIZE)
    plt.bar(agg_topo.index, agg_topo['mean'], yerr=agg_topo['std'],
            capsize=5, color="#DD8452", edgecolor="black", alpha=0.9, width=0.6)
    plt.ylabel("Total Latency (ms)")
    plt.xlabel("Topology")
    plt.title("Mean Latency by Topology")
    plt.xticks(rotation=25, ha='right')
    save_fig(os.path.join(outdir, "latency_bar_total_topology"))

    # 2. By Operation
    agg_op = df2.groupby(schema.op)["_total"].agg(['mean', 'std']).sort_values(by='mean', ascending=False)
    agg_op.index = agg_op.index.map(get_op_display_name)

    plt.figure(figsize=FIG_SIZE)
    plt.bar(agg_op.index, agg_op['mean'], yerr=agg_op['std'],
            capsize=5, color="#8172B3", edgecolor="black", alpha=0.9, width=0.6)
    plt.ylabel("Total Latency (ms)")
    plt.xlabel("Operation")
    plt.title("Mean Latency by Operation")
    plt.xticks(rotation=25, ha='right')
    save_fig(os.path.join(outdir, "latency_bar_total_operation"))

def plot_error_distribution(df: pd.DataFrame, schema: CsvSchema, outdir: str):
    if not schema.error: return
    apply_science_style()
    df2 = df.copy()
    df2["_ok"] = coerce_bool_series(df2[schema.success])

    failures = df2[~df2["_ok"]][schema.error].dropna().astype(str)
    if failures.empty: return

    def clean_err(e):
        e = str(e)
        e = re.sub(r"0x[0-9a-fA-F]+", "", e) # remove memory addresses
        e = re.sub(r"Traceback.*", "", e, flags=re.DOTALL)
        return (re.sub(r"\s+", " ", e).strip())[:50]

    counts = failures.apply(clean_err).value_counts().head(8)

    plt.figure(figsize=FIG_SIZE)
    counts.sort_values().plot(kind="barh", color="#C44E52", edgecolor="black", alpha=0.8)
    plt.xlabel("Count")
    plt.title("Distribution of Error Types")
    plt.grid(axis='x')
    plt.tight_layout()
    save_fig(os.path.join(outdir, "error_distribution"))

def plot_latency_breakdown_complete(df: pd.DataFrame, schema: CsvSchema, outdir: str):
    apply_science_style()
    components = []
    # Include Planning if available
    if schema.plan_ms: components.append(("Planning (LLM)", schema.plan_ms, "#CCB974"))
    if schema.ctrl_ms: components.append(("Controller", schema.ctrl_ms, "#64B5CD"))
    if schema.verify_ms: components.append(("Verification", schema.verify_ms, "#C44E52"))

    if not components: return

    sorted_topos = get_sorted_topologies(df, schema)
    means = {label: [] for label, _, _ in components}

    for t in sorted_topos:
        topo_df = df[df[schema.topo] == t]
        for label, col, _ in components:
            val = pd.to_numeric(topo_df[col], errors='coerce').mean()
            means[label].append(val if not pd.isna(val) else 0)

    display_labels = [get_topo_display_name(t) for t in sorted_topos]

    plt.figure(figsize=FIG_SIZE)
    bottom = np.zeros(len(sorted_topos))

    for label, _, color in components:
        values = np.array(means[label])
        plt.bar(display_labels, values, bottom=bottom, label=label, color=color, edgecolor="black", width=0.6, alpha=0.9)
        bottom += values

    plt.ylabel("Mean Latency (ms)")
    plt.xlabel("Topology")
    plt.title("Latency Breakdown (Complete)")
    plt.legend(frameon=True, loc='upper left')
    plt.xticks(rotation=25, ha='right')
    save_fig(os.path.join(outdir, "latency_breakdown_complete"))

def plot_scaling_trend(df: pd.DataFrame, schema: CsvSchema, outdir: str):
    if not schema.total_ms or not schema.topo: return

    df2 = df[df[schema.topo].str.contains("linear", case=False, na=False)].copy()
    if df2.empty: return

    def extract_hosts(topo_name):
        match = re.search(r"linear_?(\d+)", str(topo_name), re.IGNORECASE)
        return int(match.group(1)) if match else None

    if schema.n_hosts and schema.n_hosts in df2.columns:
         df2["_hosts"] = pd.to_numeric(df2[schema.n_hosts], errors='coerce')
    else:
         df2["_hosts"] = df2[schema.topo].apply(extract_hosts)

    df2 = df2.dropna(subset=["_hosts"])
    df2["_total"] = pd.to_numeric(df2[schema.total_ms], errors='coerce')

    scaling = df2.groupby("_hosts")["_total"].agg(["mean", "std"]).sort_index()
    if scaling.empty: return

    apply_science_style()
    plt.figure(figsize=FIG_SIZE)

    plt.errorbar(scaling.index, scaling["mean"], yerr=scaling["std"],
                 fmt='-o', color="#4C72B0", ecolor='gray', capsize=5, linewidth=2, markersize=8)

    plt.ylabel("Total Latency (ms)")
    plt.xlabel("Number of Hosts")
    plt.title("Latency Scaling (Linear Topology)")
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
# ONOS Metrics Logic
# -----------------------------
def process_onos_metrics(json_path: str, outdir: str):
    if not json_path: return
    try:
        with open(json_path, 'r') as f: data = json.load(f)
    except Exception as e:
        print(f"Failed to load metrics JSON: {e}")
        return

    desired_keys = ["intentSubmitted", "intentInstalled", "intentFailed", "intentWithdrawn"]

    # 1. COUNTS TABLE
    rows = []
    for key in desired_keys:
        full_key = key + "Rate"
        if full_key in data and "count" in data[full_key]:
            rows.append({
                "Metric": get_metric_display_name(key),
                "Total Count": data[full_key]["count"],
                "1-min Rate": f"{data[full_key].get('m1_rate', 0):.2f}"
            })

    if rows:
        df_metrics = pd.DataFrame(rows)
        save_plotly_table(df_metrics, "ONOS Controller Metrics", os.path.join(outdir, "onos_metrics_table"))

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

    # Tables (Plotly)
    print("Generating Tables (Images)...")
    topo_tab, op_tab = summarize_success(df, schema)
    save_plotly_table(topo_tab, "Success Rates by Topology", os.path.join(args.outdir, "tables", "table_success_topology"))
    save_plotly_table(op_tab, "Success Rates by Operation", os.path.join(args.outdir, "tables", "table_success_operation"))

    err_tab = summarize_errors(df, schema)
    if not err_tab.empty:
        save_plotly_table(err_tab, "Error Type Distribution", os.path.join(args.outdir, "tables", "table_errors"))

    # Plots (Matplotlib)
    print("Generating Plots...")
    plot_success_rates(df, schema, args.outdir)
    plot_latency_bars(df, schema, args.outdir)
    plot_error_distribution(df, schema, args.outdir)
    plot_latency_breakdown_complete(df, schema, args.outdir)
    plot_scaling_trend(df, schema, args.outdir)
    plot_baseline_comparison(df, schema, args.outdir)

    if args.metrics_json:
        print("Processing ONOS metrics...")
        process_onos_metrics(args.metrics_json, args.outdir)

    print(f"Done! Check {args.outdir}")

if __name__ == "__main__":
    main()