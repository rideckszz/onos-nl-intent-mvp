# experiments/runner.py
import argparse
import json
import os
import time
from datetime import datetime, timezone
from typing import Dict, Any, List

from src.llm.client import call_llm
from src.llm.prompts import PLANNER_SYSTEM_PROMPT
from src.onos.intents import create_host_to_host_intent, delete_intent, find_intents_for_hosts, list_intents, delete_all_intents, find_intents_within_host_set
from src.onos.verify import (
    wait_for_intent_present_between,
    wait_for_intent_absent_between,
    wait_for_intents_absent_for_host,
)

DEFAULT_PROMPTS = [
    "connect h1 and h2",
    "connect h1 and h3",
    "list intents",
    "remove intents from h1 and h3",
    "remove intents from h1",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def jsonl_append(path: str, record: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def run_one(prompt: str, verify_timeout: float) -> Dict[str, Any]:
    """
    Executes one end-to-end trial:
      prompt -> LLM plan -> dispatch ONOS -> verify -> record timing + results
    """
    record: Dict[str, Any] = {
        "ts": now_iso(),
        "prompt": prompt,
        "ok": False,
        "operation": None,
        "plan": None,
        "llm_ms": None,
        "onos_ms": None,
        "verify_ms": None,
        "total_ms": None,
        "error": None,
        "onos_result": None,
        "verify": None,
    }

    t0 = time.perf_counter()

    # 1) LLM planning
    try:
        t_llm0 = time.perf_counter()
        planner_output = call_llm(
            system_prompt=PLANNER_SYSTEM_PROMPT,
            user_prompt=prompt,
            temperature=0.0,
        )
        t_llm1 = time.perf_counter()
        record["llm_ms"] = (t_llm1 - t_llm0) * 1000.0
    except Exception as e:
        record["error"] = f"llm_call_failed: {e}"
        record["total_ms"] = (time.perf_counter() - t0) * 1000.0
        return record

    # 2) Parse plan
    try:
        plan = json.loads(planner_output)
        record["plan"] = plan
        record["operation"] = plan.get("operation")
    except Exception as e:
        record["error"] = f"plan_parse_failed: {e}"
        record["total_ms"] = (time.perf_counter() - t0) * 1000.0
        return record

    op = record["operation"]
    hosts = plan.get("hosts")
    src_host = plan.get("src_host")
    dst_host = plan.get("dst_host")
    priority = plan.get("priority") or 55

    # Backward compatibility: connect/delete using src/dst if hosts absent
    if not hosts:
        if op == "connect_hosts" and src_host and dst_host:
            hosts = [src_host, dst_host]
        elif op == "delete_intents_between_hosts" and src_host:
            hosts = [src_host] + ([dst_host] if dst_host else [])

    # 3) Dispatch
    t_onos0 = time.perf_counter()
    try:
        if op == "connect_hosts":
            if not hosts or len(hosts) < 2:
                raise ValueError(f"connect_hosts requires hosts>=2; got {hosts}")

            # MVP: only support 2-host connect in experiments runner to keep verification simple.
            # If hosts>2, connect in mesh and verify each pair.
            created = []
            for i in range(len(hosts)):
                for j in range(i + 1, len(hosts)):
                    resp = create_host_to_host_intent(hosts[i], hosts[j], priority)
                    created.append({"a": hosts[i], "b": hosts[j], "resp": resp})
            record["onos_result"] = {"created": created}

        elif op == "delete_intents_between_hosts":
            if not hosts or len(hosts) not in (1, 2):
                raise ValueError(f"delete_intents_between_hosts requires hosts len 1 or 2; got {hosts}")

            match_mode = "all" if len(hosts) == 2 else "any"
            matches = find_intents_for_hosts(
                host_tokens=hosts,
                match_mode=match_mode,
                app_id="org.onosproject.ovsdb",
                intent_type="HostToHostIntent",
            )

            deleted = []
            for intent in matches:
                intent_id = intent.get("id") or intent.get("key")
                app_id = intent.get("appId", "org.onosproject.ovsdb")
                resp = delete_intent(app_id, intent_id)
                deleted.append({"appId": app_id, "id": intent_id, "resp": resp, "resources": intent.get("resources", [])})

            record["onos_result"] = {"matched": len(matches), "deleted": deleted}

        elif op == "list_intents":
            record["onos_result"] = list_intents()

        elif op == "delete_all_intents":
            results = delete_all_intents(app_id="org.onosproject.ovsdb")
            record["onos_result"] = {"deleted": results}


        else:
            raise ValueError(f"unsupported_operation: {op}")
    except Exception as e:
        t_onos1 = time.perf_counter()
        record["onos_ms"] = (t_onos1 - t_onos0) * 1000.0
        record["error"] = f"onos_dispatch_failed: {e}"
        record["total_ms"] = (time.perf_counter() - t0) * 1000.0
        return record

    t_onos1 = time.perf_counter()
    record["onos_ms"] = (t_onos1 - t_onos0) * 1000.0

    # 4) Verification
    t_v0 = time.perf_counter()
    try:
        if op == "connect_hosts":
            # Verify each created pair is present
            results = []
            for item in record["onos_result"]["created"]:
                a, b = item["a"], item["b"]
                ok, matches = wait_for_intent_present_between(a, b, timeout_s=verify_timeout)
                results.append({"a": a, "b": b, "ok": ok, "matches": matches})
            record["verify"] = results
            record["ok"] = all(r["ok"] for r in results)

        elif op == "delete_intents_between_hosts":
            if len(hosts) == 2:
                ok, remaining = wait_for_intent_absent_between(hosts[0], hosts[1], timeout_s=verify_timeout)
                record["verify"] = {"mode": "pair", "ok": ok, "remaining": remaining}
                record["ok"] = ok
            else:
                ok, remaining = wait_for_intents_absent_for_host(hosts[0], timeout_s=verify_timeout)
                record["verify"] = {"mode": "single", "ok": ok, "remaining": remaining}
                record["ok"] = ok

        elif op == "list_intents":
            # Nothing to "verify" beyond successful call
            record["verify"] = {"ok": True}
            record["ok"] = True

    except Exception as e:
        record["verify"] = {"ok": False, "error": str(e)}
        record["ok"] = False
    finally:
        t_v1 = time.perf_counter()
        record["verify_ms"] = (t_v1 - t_v0) * 1000.0

    # 5) Totals
    record["total_ms"] = (time.perf_counter() - t0) * 1000.0
    return record


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=10, help="Number of total trials (each trial runs one prompt).")
    ap.add_argument("--verify-timeout", type=float, default=5.0, help="Verification timeout in seconds.")
    ap.add_argument("--out", type=str, default="logs/experiments.jsonl", help="Output JSONL file path.")
    ap.add_argument("--prompts", type=str, default="", help="Optional JSON array string of prompts to cycle through.")
    args = ap.parse_args()

    if args.prompts.strip():
        prompts = json.loads(args.prompts)
        if not isinstance(prompts, list) or not all(isinstance(p, str) for p in prompts):
            raise ValueError("--prompts must be a JSON array of strings")
    else:
        prompts = DEFAULT_PROMPTS

    print(f"Writing logs to: {args.out}")
    print(f"Prompts: {prompts}")
    print(f"Trials: {args.trials}")

    for i in range(args.trials):
        prompt = prompts[i % len(prompts)]
        rec = run_one(prompt, verify_timeout=args.verify_timeout)
        jsonl_append(args.out, rec)

        status = "OK" if rec.get("ok") else "FAIL"
        print(f"[{i+1}/{args.trials}] {status} op={rec.get('operation')} prompt={prompt!r} total_ms={rec.get('total_ms'):.1f}")


if __name__ == "__main__":
    main()
