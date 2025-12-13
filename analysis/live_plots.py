# experiments/live_plot_scaling.py

import argparse
import csv
import os
import time
from typing import Dict, List

import matplotlib.pyplot as plt
import statistics


def load_rows(csv_path: str) -> List[Dict]:
    if not os.path.exists(csv_path):
        return []

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    parsed = []
    for r in rows:
        try:
            r_parsed = {
                "topology_label": r.get("topology_label", ""),
                "hosts_count": int(r.get("hosts_count", "0") or 0),
                "run_id": int(r.get("run_id", "0") or 0),
                "prompt": r.get("prompt", ""),
                "operation": r.get("operation", "") or "None",
                "ok": str(r.get("ok", "")).strip().lower() in ("true", "1", "yes"),
                "error": r.get("error", ""),
                "total_ms": float(r.get("total_ms", "0") or 0.0),
                "llm_ms": float(r.get("llm_ms", "0") or 0.0),
                "onos_ms": float(r.get("onos_ms", "0") or 0.0),
                "verify_ms": float(r.get("verify_ms", "0") or 0.0),
            }
            parsed.append(r_parsed)
        except Exception:
            # Skip malformed lines
            continue

    return parsed


def summarise(rows: List[Dict], topo_filter: str = None) -> None:
    if topo_filter:
        rows = [r for r in rows if r["topology_label"] == topo_filter]

    if not rows:
        print("[INFO] No data yet for summary.")
        return

    ops = sorted(set(r["operation"] for r in rows))
    print("=" * 60)
    if topo_filter:
        print(f"[SUMMARY] Topology: {topo_filter}  (rows={len(rows)})")
    else:
        print(f"[SUMMARY] All topologies  (rows={len(rows)})")

    for op in ops:
        op_rows = [r for r in rows if r["operation"] == op]
        if not op_rows:
            continue
        succ = sum(1 for r in op_rows if r["ok"])
        total = len(op_rows)
        total_ms_vals = [r["total_ms"] for r in op_rows if r["total_ms"] > 0]
        llm_ms_vals = [r["llm_ms"] for r in op_rows if r["llm_ms"] > 0]
        onos_ms_vals = [r["onos_ms"] for r in op_rows if r["onos_ms"] > 0]

        def med_or_dash(vals):
            return f"{statistics.median(vals):.1f}" if vals else "-"

        print(f"\nOperation: {op}")
        print(f"  Success: {succ}/{total} ({(succ/total*100):.1f}%)")
        print(f"  Median total_ms: {med_or_dash(total_ms_vals)}")
        print(f"  Median llm_ms:   {med_or_dash(llm_ms_vals)}")
        print(f"  Median onos_ms:  {med_or_dash(onos_ms_vals)}")


def live_plot(csv_path: str, refresh: float, all_topologies: bool) -> None:
    plt.ion()
    fig, ax = plt.subplots()
    last_n_rows = 0

    try:
        while True:
            rows = load_rows(csv_path)

            if not rows:
                ax.clear()
                ax.set_title("Waiting for data...")
                ax.set_xlabel("run_id")
                ax.set_ylabel("total_ms")
                fig.canvas.draw()
                fig.canvas.flush_events()
                time.sleep(refresh)
                continue

            # Use the latest topology as default focus
            latest_topo = rows[-1]["topology_label"] if rows[-1]["topology_label"] else None
            if all_topologies:
                rows_to_plot = rows
                title_suffix = "(all topologies)"
            else:
                rows_to_plot = [r for r in rows if r["topology_label"] == latest_topo]
                title_suffix = f"(topology={latest_topo})"

            # Only re-print summary if new rows were added
            if len(rows) != last_n_rows:
                summarise(rows, None if all_topologies else latest_topo)
                last_n_rows = len(rows)

            # Filter out rows without a valid operation
            rows_to_plot = [r for r in rows_to_plot if r["operation"] and r["operation"] != "None"]

            ax.clear()

            if not rows_to_plot:
                ax.set_title(f"No valid operations yet {title_suffix}")
                ax.set_xlabel("run_id")
                ax.set_ylabel("total_ms")
                fig.canvas.draw()
                fig.canvas.flush_events()
                time.sleep(refresh)
                continue

            ops = sorted(set(r["operation"] for r in rows_to_plot))
            for op in ops:
                op_rows = [r for r in rows_to_plot if r["operation"] == op]
                xs = [r["run_id"] for r in op_rows]
                ys = [r["total_ms"] for r in op_rows]
                # Mark failures with open markers
                markers = ["o" if r["ok"] else "x" for r in op_rows]

                # Plot each point so we can distinguish failures
                for x, y, m in zip(xs, ys, markers):
                    ax.plot(x, y, marker=m, linestyle="None", label=op)

            # Avoid duplicate legend entries
            handles, labels = ax.get_legend_handles_labels()
            uniq = dict(zip(labels, handles))
            ax.legend(uniq.values(), uniq.keys(), loc="upper left")

            ax.set_xlabel("run_id")
            ax.set_ylabel("total_ms")
            ax.set_title(f"NL-to-intent latency {title_suffix}")

            fig.canvas.draw()
            fig.canvas.flush_events()

            time.sleep(refresh)
    except KeyboardInterrupt:
        print("\n[INFO] Live plotting interrupted by user.")
    finally:
        plt.ioff()
        plt.show()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Live plotting for topology_scaling.csv (NL-to-intent experiments)."
    )
    p.add_argument(
        "--csv",
        default="logs/experiments_scaling.csv",
        help="Path to the CSV file generated by experiments.topology_scaling.",
    )
    p.add_argument(
        "--refresh",
        type=float,
        default=3.0,
        help="Refresh interval in seconds.",
    )
    p.add_argument(
        "--all-topologies",
        action="store_true",
        help="If set, plot all topologies together instead of focusing on the latest one.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    print(f"[INFO] Watching CSV: {args.csv}")
    print(f"[INFO] Refresh interval: {args.refresh}s")
    if args.all_topologies:
        print("[INFO] Mode: all topologies")
    else:
        print("[INFO] Mode: focus on latest topology in the CSV")
    live_plot(args.csv, args.refresh, args.all_topologies)


if __name__ == "__main__":
    main()
