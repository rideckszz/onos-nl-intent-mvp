# experiments/topology_scaling.py
"""
Scalability experiment: automatically create Mininet topologies,
attach them to ONOS, and run NL-to-intent latency experiments.

Run (as root, from project root):

    sudo -E python -m experiments.topology_scaling \
        --runs-per-op 10 \
        --warmup-intents 5 \
        --csv logs/experiments_scaling.csv

Optional: restrict to specific topologies by label:

    sudo -E python -m experiments.topology_scaling \
        --runs-per-op 10 \
        --warmup-intents 5 \
        --csv logs/experiments_scaling.csv \
        --topologies linear_3 linear_9
"""

import argparse
import csv
import json
import os
import random
import time
from typing import Dict, List, Tuple

import requests

from src.llm.client import call_llm
from src.llm.prompts import PLANNER_SYSTEM_PROMPT
from src.onos.intents import (
    create_host_to_host_intent,
    list_intents,
    delete_intent,
    find_intents_for_hosts,
    find_intents_within_host_set,
    delete_all_intents,
)

# Mininet imports (must run as root)
from mininet.net import Mininet
from mininet.topo import Topo, LinearTopo
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink


# Force OpenFlow13 on all OVS switches, like: --switch ovsk,protocols=OpenFlow13
class OVSK13Switch(OVSSwitch):
    def __init__(self, *args, **kwargs):
        # If no protocols explicitly specified, force OpenFlow13
        kwargs.setdefault("protocols", "OpenFlow13")
        super().__init__(*args, **kwargs)


# ---------------------- Root check ----------------------

if os.geteuid() != 0:
    raise SystemExit(
        "This script must be run as root because Mininet requires it.\n"
        "Use: sudo -E python -m experiments.topology_scaling ..."
    )


# ---------------------- Environment / config ----------------------

# ONOS REST API base
ONOS_BASE_URL = os.environ.get("ONOS_BASE_URL", "http://localhost:8181/onos/v1")
ONOS_USER = os.environ.get("ONOS_USER", "onos")
ONOS_PASS = os.environ.get("ONOS_PASS", "rocks")

# ONOS controller (for Mininet)
CONTROLLER_IP = os.environ.get("ONOS_CONTROLLER_IP", "127.0.0.1")
CONTROLLER_PORT = int(os.environ.get("ONOS_CONTROLLER_PORT", "6653"))

# Topologies to run in sequence
TOPOLOGIES = [
    {"label": "linear_3", "kind": "linear", "k": 3},
    {"label": "linear_6", "kind": "linear", "k": 6},
    {"label": "linear_9", "kind": "linear", "k": 9},
    {"label": "tree_d2_f2", "kind": "tree", "depth": 2, "fanout": 2},
    {"label": "tree_d2_f3", "kind": "tree", "depth": 2, "fanout": 3},
]


# ---------------------- Custom tree topology ----------------------

class SimpleTreeTopo(Topo):
    """
    Minimal tree topology:
    depth >= 1, fanout >= 1

    depth=1: one switch with 'fanout' hosts
    depth=2: root switch -> fanout switches -> each has 'fanout' hosts, etc.
    Hosts are named h1, h2, ...; switches s0, s1, ...
    """

    def build(self, depth: int = 2, fanout: int = 2):
        if depth < 1:
            raise ValueError("depth must be >= 1")
        if fanout < 1:
            raise ValueError("fanout must be >= 1")

        switch_id = 1
        host_id = 1

        def add_level(parent_switch: str, level: int):
            nonlocal switch_id, host_id
            if level == depth:
                # Attach hosts to this switch
                for _ in range(fanout):
                    host = self.addHost(f"h{host_id}")
                    host_id += 1
                    self.addLink(parent_switch, host)
            else:
                # Create child switches, recurse
                for _ in range(fanout):
                    sw = self.addSwitch(f"s{switch_id}")
                    switch_id += 1
                    self.addLink(parent_switch, sw)
                    add_level(sw, level + 1)

        # Root switch
        root = self.addSwitch("s0")
        add_level(root, 1)


# ---------------------- Helpers ----------------------

def _strip_markdown_fence(text: str) -> str:
    """
    If the LLM returns JSON inside ``` or ```json fences, strip them.
    Otherwise, return the text unchanged.
    """
    if not text:
        return text

    s = text.strip()
    if not s.startswith("```"):
        return s

    lines = s.splitlines()
    if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].startswith("```"):
        # Drop first and last fence lines
        inner = "\n".join(lines[1:-1]).strip()
        return inner or s

    return s


# ---------------------- ONOS host discovery ----------------------

def fetch_hosts_from_onos() -> List[Dict]:
    """GET /hosts from ONOS and return the raw host list."""
    url = f"{ONOS_BASE_URL}/hosts"
    resp = requests.get(url, auth=(ONOS_USER, ONOS_PASS), timeout=5)
    resp.raise_for_status()
    data = resp.json()
    return data.get("hosts", [])


def discover_host_tokens() -> List[str]:
    """
    Build a sorted list of host tokens like ['h1', 'h2', ...] from ONOS /hosts.

    Strategy:
    - Prefer IP-based mapping (10.0.0.X -> hX) if ipAddresses are present.
    - If ipAddresses is empty, fall back to MAC:
      00:00:00:00:00:03 -> h3 (use last hex byte).
    """
    hosts = fetch_hosts_from_onos()
    tokens: List[str] = []

    for h in hosts:
        token = None

        # 1) Try IP-based mapping first (if available)
        ips = h.get("ipAddresses") or []
        if ips:
            ip = ips[0]
            last_octet = ip.split(".")[-1]
            try:
                idx = int(last_octet)
                token = f"h{idx}"
            except ValueError:
                token = None

        # 2) If no IPs (or failed), fall back to MAC-based mapping
        if token is None:
            mac = h.get("mac")
            if mac:
                last_hex = mac.split(":")[-1]
                try:
                    idx = int(last_hex, 16)
                    token = f"h{idx}"
                except ValueError:
                    token = None

        if token:
            tokens.append(token)

    tokens = sorted(set(tokens), key=lambda t: int(t[1:]))
    return tokens


# ---------------------- NL execution core ----------------------

def execute_nl_request(
    user_req: str,
    topology_label: str,
    hosts_count: int,
    run_id: int,
) -> Dict:
    """
    Execute one natural-language request end-to-end:
    - call LLM planner
    - execute ONOS operation(s)
    - verify result (for connect/delete)
    Returns a dict ready to be written as one CSV row.
    """
    row: Dict = {
        "topology_label": topology_label,
        "hosts_count": hosts_count,
        "run_id": run_id,
        "prompt": user_req,
        "operation": None,
        "ok": False,
        "error": "",
        "total_ms": 0.0,
        "llm_ms": 0.0,
        "onos_ms": 0.0,
        "verify_ms": 0.0,
    }

    t_total_start = time.perf_counter()

    # 1) LLM planner
    try:
        t_llm_start = time.perf_counter()
        planner_output = call_llm(
            system_prompt=PLANNER_SYSTEM_PROMPT,
            user_prompt=user_req,
            temperature=0.0,
        )
        t_llm_end = time.perf_counter()
        row["llm_ms"] = (t_llm_end - t_llm_start) * 1000.0

        raw = _strip_markdown_fence(planner_output)
        plan = json.loads(raw)
    except Exception as e:
        row["error"] = f"LLM/planner error: {e}"
        row["total_ms"] = (time.perf_counter() - t_total_start) * 1000.0
        return row

    operation = plan.get("operation") or "other"
    row["operation"] = operation

    onos_ms = 0.0
    verify_ms = 0.0

    # 2) Dispatch ONOS operation
    try:
        if operation == "connect_hosts":
            hosts = plan.get("hosts") or []
            if not hosts or len(hosts) < 2:
                src_host = plan.get("src_host")
                dst_host = plan.get("dst_host")
                hosts = [src_host, dst_host] if src_host and dst_host else []

            if len(hosts) < 2:
                raise ValueError(f"Planner returned insufficient hosts: {hosts}")

            h1, h2 = hosts[0], hosts[1]
            priority = plan.get("priority") or 55

            # Install intent (representative pair)
            t_onos_start = time.perf_counter()
            resp = create_host_to_host_intent(h1, h2, priority)
            t_onos_end = time.perf_counter()
            onos_ms += (t_onos_end - t_onos_start) * 1000.0

            # Verify: there is an intent involving BOTH hosts
            t_v_start = time.perf_counter()
            matches = find_intents_for_hosts(
                host_tokens=[h1, h2],
                match_mode="all",
                app_id="org.onosproject.ovsdb",
                intent_type="HostToHostIntent",
            )
            verify_ok = bool(matches) and resp.get("status") in (200, 201, 202)
            t_v_end = time.perf_counter()
            verify_ms += (t_v_end - t_v_start) * 1000.0

            row["ok"] = verify_ok
            if not verify_ok:
                row["error"] = f"Verification failed for connect_hosts {h1},{h2}"

        elif operation == "delete_intents_between_hosts":
            hosts_list = plan.get("hosts") or []
            src_host = plan.get("src_host")
            dst_host = plan.get("dst_host")

            # Case 1: N-host deletion using host set (>=2 hosts in "hosts")
            if hosts_list and len(hosts_list) >= 2:
                t_onos_start = time.perf_counter()
                matches = find_intents_within_host_set(
                    host_tokens=hosts_list,
                    app_id="org.onosproject.ovsdb",
                    intent_type="HostToHostIntent",
                )

                for intent in matches:
                    intent_id = intent.get("id") or intent.get("key")
                    app_id = intent.get("appId", "org.onosproject.ovsdb")
                    if not intent_id:
                        continue
                    delete_intent(app_id, intent_id)
                t_onos_end = time.perf_counter()
                onos_ms += (t_onos_end - t_onos_start) * 1000.0

                # Verify: no intents remain whose endpoints are both in the set
                t_v_start = time.perf_counter()
                remaining = find_intents_within_host_set(
                    host_tokens=hosts_list,
                    app_id="org.onosproject.ovsdb",
                    intent_type="HostToHostIntent",
                )
                verify_ok = len(remaining) == 0
                t_v_end = time.perf_counter()
                verify_ms += (t_v_end - t_v_start) * 1000.0

                row["ok"] = verify_ok
                if not verify_ok:
                    row["error"] = (
                        f"Verification failed for delete_intents_between_hosts "
                        f"host set={hosts_list}"
                    )

            # Case 2: classic 1- or 2-host deletion via src_host/dst_host
            else:
                if not src_host and not dst_host:
                    raise ValueError(
                        "Planner did not provide src_host/dst_host or a hosts list with >= 2 items"
                    )

                host_tokens = [t for t in (src_host, dst_host) if t]
                match_mode = "all" if dst_host else "any"

                # Find & delete intents
                t_onos_start = time.perf_counter()
                matches = find_intents_for_hosts(
                    host_tokens=host_tokens,
                    match_mode=match_mode,
                    app_id="org.onosproject.ovsdb",
                    intent_type="HostToHostIntent",
                )

                for intent in matches:
                    intent_id = intent.get("id") or intent.get("key")
                    app_id = intent.get("appId", "org.onosproject.ovsdb")
                    if not intent_id:
                        continue
                    delete_intent(app_id, intent_id)
                t_onos_end = time.perf_counter()
                onos_ms += (t_onos_end - t_onos_start) * 1000.0

                # Verify: ensure no intents remain for that host set
                t_v_start = time.perf_counter()
                remaining = find_intents_for_hosts(
                    host_tokens=host_tokens,
                    match_mode=match_mode,
                    app_id="org.onosproject.ovsdb",
                    intent_type="HostToHostIntent",
                )
                verify_ok = len(remaining) == 0
                t_v_end = time.perf_counter()
                verify_ms += (t_v_end - t_v_start) * 1000.0

                row["ok"] = verify_ok
                if not verify_ok:
                    row["error"] = (
                        "Verification failed for delete_intents_between_hosts "
                        f"{host_tokens}"
                    )

        elif operation == "delete_all_intents":
            app_id = "org.onosproject.ovsdb"

            # Delete all intents for this app
            t_onos_start = time.perf_counter()
            _results = delete_all_intents(app_id=app_id)
            t_onos_end = time.perf_counter()
            onos_ms += (t_onos_end - t_onos_start) * 1000.0

            # Verify: list intents and ensure none remain for this app_id
            t_v_start = time.perf_counter()
            intents_json = list_intents()
            intents_list = (
                intents_json.get("intents", [])
                or intents_json.get("entries", [])
                or []
            )
            remaining_for_app = [
                it for it in intents_list if it.get("appId") == app_id
            ]
            verify_ok = len(remaining_for_app) == 0
            t_v_end = time.perf_counter()
            verify_ms += (t_v_end - t_v_start) * 1000.0

            row["ok"] = verify_ok
            if not verify_ok:
                row["error"] = (
                    f"delete_all_intents left {len(remaining_for_app)} "
                    f"intents for appId={app_id}"
                )

        elif operation == "list_intents":
            t_onos_start = time.perf_counter()
            _ = list_intents()
            t_onos_end = time.perf_counter()
            onos_ms += (t_onos_end - t_onos_start) * 1000.0
            row["ok"] = True

        else:
            row["ok"] = False
            row["error"] = f"Unsupported operation from planner: {operation}"

    except Exception as e:
        row["ok"] = False
        row["error"] = f"ONOS/verification error: {e}"

    row["onos_ms"] = onos_ms
    row["verify_ms"] = verify_ms
    row["total_ms"] = (time.perf_counter() - t_total_start) * 1000.0
    return row


# ---------------------- Workload helpers ----------------------

def sample_host_pair(host_tokens: List[str]) -> Tuple[str, str]:
    """Sample two distinct host tokens."""
    if len(host_tokens) < 2:
        raise RuntimeError("Need at least 2 hosts in topology for this experiment.")
    h1, h2 = random.sample(host_tokens, 2)
    return h1, h2


def format_host_set_prompt(hosts: List[str]) -> str:
    """
    Format something like ['h1','h2','h3'] as 'h1, h2 and h3'.
    Assumes len(hosts) >= 2.
    """
    if len(hosts) == 2:
        return f"{hosts[0]} and {hosts[1]}"
    head = ", ".join(hosts[:-1])
    return f"{head} and {hosts[-1]}"


def debug_onos_state() -> None:
    """Print ONOS /devices and /hosts for debugging when no hosts are visible."""
    print("[DEBUG] Fetching ONOS /devices and /hosts for diagnostics...")

    # Devices
    try:
        dev_resp = requests.get(
            f"{ONOS_BASE_URL}/devices",
            auth=(ONOS_USER, ONOS_PASS),
            timeout=5,
        )
        dev_resp.raise_for_status()
        dev_json = dev_resp.json()
        print("[DEBUG] /devices:")
        print(json.dumps(dev_json, indent=2))
    except Exception as e:
        print(f"[DEBUG] Failed to fetch /devices: {e}")

    # Hosts
    try:
        host_resp = requests.get(
            f"{ONOS_BASE_URL}/hosts",
            auth=(ONOS_USER, ONOS_PASS),
            timeout=5,
        )
        host_resp.raise_for_status()
        host_json = host_resp.json()
        print("[DEBUG] /hosts:")
        print(json.dumps(host_json, indent=2))
    except Exception as e:
        print(f"[DEBUG] Failed to fetch /hosts: {e}")


def run_scaling_experiment(
    topology_label: str,
    runs_per_op: int,
    warmup_intents: int,
    csv_path: str,
    seed: int,
) -> None:
    random.seed(seed)

    # 1) Wait for ONOS to actually see hosts
    max_attempts = 5
    host_tokens: List[str] = []
    hosts_count = 0

    for attempt in range(1, max_attempts + 1):
        try:
            host_tokens = discover_host_tokens()
            hosts_count = len(host_tokens)
        except Exception as e:
            print(
                f"[WARN] Failed to fetch hosts from ONOS on attempt "
                f"{attempt}/{max_attempts}: {e}"
            )
            host_tokens = []
            hosts_count = 0

        if hosts_count >= 2:
            break

        print(
            f"[WARN] ONOS currently sees {hosts_count} hosts. "
            f"Attempt {attempt}/{max_attempts}. Waiting 2s..."
        )
        time.sleep(2)

    if hosts_count < 2:
        print(
            f"[ERROR] Topology '{topology_label}' still has {hosts_count} hosts "
            f"visible in ONOS /hosts after retries."
        )
        debug_onos_state()
        raise RuntimeError(
            f"Topology '{topology_label}' has {hosts_count} hosts visible in ONOS /hosts.\n"
            f"Please ensure Mininet is running, attached to ONOS, and that a forwarding app is active."
        )

    print(f"[INFO] Topology '{topology_label}': discovered hosts: {host_tokens}")
    print(f"[INFO] Total hosts: {hosts_count}")
    print(f"[INFO] Warm-up intents to pre-install: {warmup_intents}")

    # 2) Warm-up: pre-install some intents so ONOS state isn't empty
    for i in range(warmup_intents):
        h1, h2 = sample_host_pair(host_tokens)
        try:
            _ = create_host_to_host_intent(h1, h2, priority=55)
        except Exception as e:
            print(f"[WARN] Warm-up intent {i+1}/{warmup_intents} failed: {e}")

    print("[INFO] Warm-up complete. Starting measured runs.")

    # 3) Prepare CSV
    file_exists = os.path.exists(csv_path)
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    csv_file = open(csv_path, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(
        csv_file,
        fieldnames=[
            "topology_label",
            "hosts_count",
            "run_id",
            "prompt",
            "operation",
            "ok",
            "error",
            "total_ms",
            "llm_ms",
            "onos_ms",
            "verify_ms",
        ],
    )
    if not file_exists:
        writer.writeheader()

    run_id = 0

    def log_run(prompt: str) -> None:
        nonlocal run_id
        run_id += 1
        row = execute_nl_request(
            user_req=prompt,
            topology_label=topology_label,
            hosts_count=hosts_count,
            run_id=run_id,
        )
        writer.writerow(row)
        csv_file.flush()
        status = "OK" if row["ok"] else "FAIL"
        print(
            f"[{status}] #{run_id} topo={topology_label} op={row['operation']} "
            f"total={row['total_ms']:.1f}ms (llm={row['llm_ms']:.1f}ms, "
            f"onos={row['onos_ms']:.1f}ms, verify={row['verify_ms']:.1f}ms)"
        )
        if row["error"]:
            print(f"       error: {row['error']}")

    # 4a) connect_hosts runs (2-host requests)
    print(f"[INFO] Running {runs_per_op} x 'connect_hosts' requests...")
    for _ in range(runs_per_op):
        h1, h2 = sample_host_pair(host_tokens)
        prompt = f"connect {h1} and {h2}"
        log_run(prompt)

    # 4b) list_intents runs
    print(f"[INFO] Running {runs_per_op} x 'list intents' requests...")
    for _ in range(runs_per_op):
        prompt = "list intents"
        log_run(prompt)

    # 4c) delete_intents_between_hosts runs with 1, 2, and (if possible) 3-host patterns
    print(
        f"[INFO] Running {runs_per_op} x 'delete_intents_between_hosts' "
        f"requests (1-, 2-, and N-host variants)..."
    )
    for _ in range(runs_per_op):
        if hosts_count >= 3:
            variant = random.choice(["pair", "single", "set3"])
        else:
            variant = random.choice(["pair", "single"])

        if variant == "pair":
            h1, h2 = sample_host_pair(host_tokens)
            prompt = f"remove intents from {h1} and {h2}"
        elif variant == "single":
            h1, _ = sample_host_pair(host_tokens)
            prompt = f"remove intents from {h1}"
        else:  # "set3"
            hs = random.sample(host_tokens, 3)
            prompt = f"remove intents from {format_host_set_prompt(hs)}"

        log_run(prompt)

    # 4d) delete_all_intents runs
    print(f"[INFO] Running {runs_per_op} x 'delete_all_intents' requests...")
    for _ in range(runs_per_op):
        prompt = "remove all intents"
        log_run(prompt)

    csv_file.close()
    print(
        f"[INFO] Experiment for topology '{topology_label}' complete. "
        f"Results appended to: {csv_path}"
    )


# ---------------------- Mininet topology builders ----------------------

def build_mininet_for_topology(cfg: Dict) -> Mininet:
    kind = cfg["kind"]
    if kind == "linear":
        k = cfg["k"]
        topo = LinearTopo(k=k)
    elif kind == "tree":
        depth = cfg["depth"]
        fanout = cfg["fanout"]
        topo = SimpleTreeTopo(depth=depth, fanout=fanout)
    else:
        raise ValueError(f"Unknown topology kind: {kind}")

    net = Mininet(
        topo=topo,
        controller=lambda name: RemoteController(
            name, ip=CONTROLLER_IP, port=CONTROLLER_PORT
        ),
        switch=OVSK13Switch,
        link=TCLink,
        autoSetMacs=True,
        autoStaticArp=True,
        build=True,
    )
    return net


def run_for_topology_cfg(
    cfg: Dict,
    runs_per_op: int,
    warmup_intents: int,
    csv_path: str,
    seed: int,
) -> None:
    label = cfg["label"]
    print("\n" + "=" * 70)
    print(f"[TOPO] Starting topology: {label} (kind={cfg['kind']})")
    print("=" * 70)

    net = build_mininet_for_topology(cfg)
    try:
        net.start()
        print(
            f"[TOPO] Mininet started for '{label}'. Attaching to ONOS at "
            f"{CONTROLLER_IP}:{CONTROLLER_PORT}"
        )

        # Give ONOS time to establish switch connections before pingAll
        print("[TOPO] Waiting 5s for switches to connect to ONOS before pingAll...")
        time.sleep(5)

        print("[TOPO] Running pingAll to trigger host discovery in ONOS...")
        net.pingAll()

        # And then wait a bit more so /hosts is fully populated
        print("[TOPO] Waiting 8s for ONOS to update /hosts...")
        time.sleep(8)

        run_scaling_experiment(
            topology_label=label,
            runs_per_op=runs_per_op,
            warmup_intents=warmup_intents,
            csv_path=csv_path,
            seed=seed,
        )
    finally:
        print(f"[TOPO] Stopping topology '{label}' and cleaning Mininet...")
        net.stop()
        os.system("mn -c >/dev/null 2>&1")
        time.sleep(2)


# ---------------------- CLI entrypoint ----------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Scalability experiment for ONOS NL intent mediator "
            "across multiple Mininet topologies."
        )
    )
    p.add_argument(
        "--runs-per-op",
        type=int,
        default=10,
        help=(
            "How many times to run each operation type "
            "(connect, list, delete, delete_all) per topology."
        ),
    )
    p.add_argument(
        "--warmup-intents",
        type=int,
        default=5,
        help=(
            "How many HostToHostIntents to create as warm-up before measuring "
            "(per topology)."
        ),
    )
    p.add_argument(
        "--csv",
        default="logs/experiments_scaling.csv",
        help="Path to the CSV file where results will be appended.",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=123,
        help="Random seed for host pair sampling.",
    )
    p.add_argument(
        "--topologies",
        nargs="*",
        help="Optional list of topology labels to run (subset of TOPOLOGIES). Default: all.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    labels_filter = set(args.topologies) if args.topologies else None

    for cfg in TOPOLOGIES:
        if labels_filter and cfg["label"] not in labels_filter:
            continue
        run_for_topology_cfg(
            cfg=cfg,
            runs_per_op=args.runs_per_op,
            warmup_intents=args.warmup_intents,
            csv_path=args.csv,
            seed=args.seed,
        )

    print("\n[ALL DONE] All requested topologies have been executed.")


if __name__ == "__main__":
    main()
