# analysis/metrics_export.py
import argparse
import csv
import json
import math
from collections import defaultdict
from typing import Any, Dict, List, Tuple


def load_jsonl(path: str) -> List[Dict[str, Any]]:
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def percentile(values: List[float], p: float) -> float:
    if not values:
        return float("nan")
    values = sorted(values)
    k = (len(values) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return values[int(k)]
    return values[f] + (values[c] - values[f]) * (k - f)


def summarize_latencies(rows: List[Dict[str, Any]], key: str) -> Dict[str, float]:
    vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
    return {
        "count": float(len(vals)),
        "mean_ms": sum(vals) / len(vals) if vals else float("nan"),
        "p50_ms": percentile(vals, 0.50),
        "p90_ms": percentile(vals, 0.90),
        "p99_ms": percentile(vals, 0.99),
        "min_ms": min(vals) if vals else float("nan"),
        "max_ms": max(vals) if vals else float("nan"),
    }


def write_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    # Flatten a few common fields for spreadsheet import
    fieldnames = [
        "ts", "prompt", "operation", "ok",
        "llm_ms", "onos_ms", "verify_ms", "total_ms",
        "error",
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fieldnames})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True, help="Input JSONL (logs/experiments.jsonl).")
    ap.add_argument("--out-csv", default="logs/experiments.csv", help="Output CSV summary.")
    args = ap.parse_args()

    rows = load_jsonl(args.inp)
    if not rows:
        print("No rows found.")
        return

    # Basic outcome metrics
    total = len(rows)
    ok = sum(1 for r in rows if r.get("ok") is True)
    fail = total - ok

    by_op = defaultdict(list)
    for r in rows:
        by_op[r.get("operation", "unknown")].append(r)

    print("=== Experiment Summary ===")
    print(f"Total trials: {total}")
    print(f"Success: {ok} ({ok/total*100:.1f}%)")
    print(f"Fail: {fail} ({fail/total*100:.1f}%)")

    print("\n=== Success Rate by Operation ===")
    for op, rs in sorted(by_op.items(), key=lambda x: x[0]):
        t = len(rs)
        o = sum(1 for r in rs if r.get("ok") is True)
        print(f"- {op}: {o}/{t} ({o/t*100:.1f}%)")

    print("\n=== Latency Summary (ms) by Operation ===")
    for op, rs in sorted(by_op.items(), key=lambda x: x[0]):
        print(f"\nOperation: {op}")
        for key in ("llm_ms", "onos_ms", "verify_ms", "total_ms"):
            s = summarize_latencies(rs, key)
            print(f"  {key}: n={int(s['count'])} mean={s['mean_ms']:.1f} p50={s['p50_ms']:.1f} p90={s['p90_ms']:.1f} p99={s['p99_ms']:.1f}")

    # Export CSV
    write_csv(args.out_csv, rows)
    print(f"\nWrote CSV: {args.out_csv}")

    # Optional: list top errors
    err_counts = defaultdict(int)
    for r in rows:
        if r.get("ok") is True:
            continue
        e = r.get("error") or "unknown_error"
        err_counts[e] += 1

    if err_counts:
        print("\n=== Top Errors ===")
        for e, c in sorted(err_counts.items(), key=lambda x: -x[1])[:10]:
            print(f"- {c}x {e}")


if __name__ == "__main__":
    main()
