# dashboard_server.py
#
# Flask dashboard for NL-to-intent experiments.
# Reads logs/experiments_scaling.csv and renders plots for the metrics
# defined in the Methodology section.

import io
import os
import time
from typing import Tuple

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from flask import Flask, render_template, send_file

CSV_PATH = "logs/experiments_scaling.csv"

app = Flask(__name__)


# -------------------------------------------------------------------
# Style and data loading
# -------------------------------------------------------------------

def configure_style() -> None:
    """Configure a simple, high-contrast scientific style for all plots."""
    matplotlib.rcdefaults()
    matplotlib.rcParams.update({
        "figure.figsize": (6.4, 4.0),
        "figure.dpi": 110,
        "axes.titlesize": 11,
        "axes.labelsize": 10,
        "legend.fontsize": 9,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "axes.grid": True,
        "grid.linestyle": ":",
        "grid.alpha": 0.35,
        "axes.facecolor": "#10141c",
        "figure.facecolor": "#10141c",
        "savefig.facecolor": "#10141c",
        "axes.edgecolor": "#e0e6ff",
        "xtick.color": "#e0e6ff",
        "ytick.color": "#e0e6ff",
        "text.color": "#e0e6ff",
        "axes.labelcolor": "#e0e6ff",
        "axes.titlecolor": "#e0e6ff",
        "axes.prop_cycle": matplotlib.cycler(color=[
            "#4e79a7", "#f28e2b", "#e15759", "#76b7b2",
            "#59a14f", "#edc949", "#af7aa1", "#ff9da7"
        ]),
    })


# Simple cache so we do not reload CSV on every plot if unchanged
_LAST_MTIME = None
_LAST_DF = None


def load_data() -> pd.DataFrame:
    global _LAST_MTIME, _LAST_DF
    if not os.path.exists(CSV_PATH):
        return pd.DataFrame()

    mtime = os.path.getmtime(CSV_PATH)
    if _LAST_DF is not None and _LAST_MTIME == mtime:
        return _LAST_DF

    df = pd.read_csv(CSV_PATH)
    # Normalise types
    df["ok"] = df["ok"].astype(bool)
    df["operation"] = df["operation"].astype(str)
    df["topology_label"] = df["topology_label"].astype(str)
    df["hosts_count"] = df["hosts_count"].astype(int)
    _LAST_MTIME = mtime
    _LAST_DF = df
    return df


def fig_to_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


# -------------------------------------------------------------------
# Error classification
# -------------------------------------------------------------------

def classify_error(msg: str) -> str:
    """Coarse-grained classification of error messages."""
    if not isinstance(msg, str) or not msg.strip():
        return "none"

    m = msg.lower()

    if "llm/planner error" in m or "planner error" in m:
        if "timed out" in m or "timeout" in m:
            return "planner_timeout"
        if "json" in m or "valid json" in m:
            return "planner_json"
        return "planner_other"

    if "onos/verification error" in m:
        if "connectionpool" in m or "failed to establish a new connection" in m:
            return "onos_connection"
        if "404" in m or "not found" in m:
            return "onos_not_found"
        return "onos_logic"

    if "verification failed" in m:
        return "verification_failed"

    return "other"


# -------------------------------------------------------------------
# Plot functions
# -------------------------------------------------------------------

def plot_latency_distribution_by_operation(df: pd.DataFrame):
    configure_style()
    fig, ax = plt.subplots()

    if df.empty:
        ax.text(0.5, 0.5, "No data available.", ha="center", va="center")
        ax.set_axis_off()
        return fig_to_png(fig)

    ops = sorted(df["operation"].unique())
    data = [df.loc[df["operation"] == op, "total_ms"] for op in ops]

    bps = ax.boxplot(
        data,
        labels=ops,
        patch_artist=True,
        medianprops={"color": "#ffffff"},
        boxprops={"linewidth": 1.2},
        whiskerprops={"linewidth": 1.0},
        capprops={"linewidth": 1.0},
    )

    for patch in bps["boxes"]:
        patch.set_facecolor("#24324a")

    ax.set_ylabel("total latency (ms)")
    ax.set_title("Latency distribution by operation (all topologies)")
    return fig_to_png(fig)


def plot_latency_components_per_operation(df: pd.DataFrame):
    configure_style()
    fig, ax = plt.subplots()

    if df.empty:
        ax.text(0.5, 0.5, "No data available.", ha="center", va="center")
        ax.set_axis_off()
        return fig_to_png(fig)

    grouped = df.groupby("operation")[["llm_ms", "onos_ms", "verify_ms"]].median()
    ops = grouped.index.tolist()
    x = range(len(ops))
    width = 0.25

    ax.bar([i - width for i in x], grouped["llm_ms"], width=width, label="LLM planning")
    ax.bar(x, grouped["onos_ms"], width=width, label="ONOS operations")
    ax.bar([i + width for i in x], grouped["verify_ms"], width=width, label="Verification")

    ax.set_xticks(list(x))
    ax.set_xticklabels(ops, rotation=10)
    ax.set_ylabel("median latency (ms)")
    ax.set_title("Latency breakdown by operation (medians)")
    ax.legend()
    return fig_to_png(fig)


def plot_scaling_with_topology(df: pd.DataFrame):
    configure_style()
    fig, ax = plt.subplots()

    if df.empty:
        ax.text(0.5, 0.5, "No data available.", ha="center", va="center")
        ax.set_axis_off()
        return fig_to_png(fig)

    # median total latency per operation and hosts_count
    grouped = (
        df.groupby(["operation", "hosts_count"])["total_ms"]
        .median()
        .reset_index()
        .sort_values("hosts_count")
    )

    ops = sorted(grouped["operation"].unique())
    for op in ops:
        sub = grouped[grouped["operation"] == op]
        ax.plot(
            sub["hosts_count"],
            sub["total_ms"],
            marker="o",
            linestyle="-",
            label=op,
        )

    ax.set_xlabel("number of hosts in topology")
    ax.set_ylabel("median total latency (ms)")
    ax.set_title("Scaling with topology size")
    ax.legend()
    return fig_to_png(fig)


def plot_success_per_operation(df: pd.DataFrame):
    configure_style()
    fig, ax = plt.subplots()

    if df.empty:
        ax.text(0.5, 0.5, "No data available.", ha="center", va="center")
        ax.set_axis_off()
        return fig_to_png(fig)

    grouped = df.groupby("operation")["ok"].mean().sort_index() * 100.0
    ops = grouped.index.tolist()
    vals = grouped.values

    bars = ax.bar(ops, vals)
    for bar, val in zip(bars, vals):
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            bar.get_height() + 1,
            f"{val:.1f}%",
            ha="center",
            va="bottom",
        )

    ax.set_ylim(0, 105)
    ax.set_ylabel("success rate (%)")
    ax.set_title("Success rate per operation (all topologies)")
    return fig_to_png(fig)


def plot_success_per_operation_topology(df: pd.DataFrame):
    """Heatmap of success rate by operation and topology."""
    configure_style()
    fig, ax = plt.subplots()

    if df.empty:
        ax.text(0.5, 0.5, "No data available.", ha="center", va="center")
        ax.set_axis_off()
        return fig_to_png(fig)

    pivot = (
        df.groupby(["topology_label", "operation"])["ok"]
        .mean()
        .mul(100.0)
        .unstack(fill_value=0.0)
        .sort_index()
    )

    im = ax.imshow(pivot.values, aspect="auto", cmap="viridis", vmin=0, vmax=100)
    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels(pivot.columns, rotation=15)
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels(pivot.index)

    for i in range(pivot.shape[0]):
        for j in range(pivot.shape[1]):
            val = pivot.values[i, j]
            ax.text(j, i, f"{val:.1f}", ha="center", va="center", color="white")

    ax.set_xlabel("operation")
    ax.set_ylabel("topology")
    ax.set_title("Success rate per operation and topology")
    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label("success rate (%)")
    return fig_to_png(fig)


def plot_error_types(df: pd.DataFrame):
    configure_style()
    fig, ax = plt.subplots()

    if df.empty:
        ax.text(0.5, 0.5, "No data available.", ha="center", va="center")
        ax.set_axis_off()
        return fig_to_png(fig)

    df_err = df.copy()
    df_err["error_type"] = df_err["error"].apply(classify_error)
    counts = df_err["error_type"].value_counts().sort_index()

    # If only "none" and/or "other", tell the user.
    if len(counts) <= 1:
        ax.text(
            0.5,
            0.5,
            "No structured errors observed so far.",
            ha="center",
            va="center",
        )
        ax.set_axis_off()
        return fig_to_png(fig)

    bars = ax.bar(counts.index, counts.values)
    for bar, val in zip(bars, counts.values):
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            bar.get_height() + 0.5,
            str(val),
            ha="center",
            va="bottom",
        )

    ax.set_ylabel("count")
    ax.set_title("Error types across all experiments")
    return fig_to_png(fig)


# -------------------------------------------------------------------
# Flask routes
# -------------------------------------------------------------------

@app.route("/")
def index():
    df = load_data()
    total_runs = len(df)
    overall_success = float(df["ok"].mean() * 100.0) if total_runs > 0 else 0.0
    topologies = sorted(df["topology_label"].unique()) if total_runs > 0 else []
    return render_template(
        "index.html",
        total_runs=total_runs,
        overall_success=overall_success,
        topologies=topologies,
        csv_path=CSV_PATH,
        refresh_interval=10,
    )

@app.route("/plot/latency_distribution.png")
def latency_distribution_png():
    df = load_data()
    return send_file(
        plot_latency_distribution_by_operation(df),
        mimetype="image/png",
    )


@app.route("/plot/latency_components.png")
def latency_components_png():
    df = load_data()
    return send_file(
        plot_latency_components_per_operation(df),
        mimetype="image/png",
    )


@app.route("/plot/scaling_topology.png")
def scaling_topology_png():
    df = load_data()
    return send_file(
        plot_scaling_with_topology(df),
        mimetype="image/png",
    )


@app.route("/plot/success_operation.png")
def success_operation_png():
    df = load_data()
    return send_file(
        plot_success_per_operation(df),
        mimetype="image/png",
    )


@app.route("/plot/success_operation_topology.png")
def success_operation_topology_png():
    df = load_data()
    return send_file(
        plot_success_per_operation_topology(df),
        mimetype="image/png",
    )


@app.route("/plot/error_types.png")
def error_types_png():
    df = load_data()
    return send_file(
        plot_error_types(df),
        mimetype="image/png",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
