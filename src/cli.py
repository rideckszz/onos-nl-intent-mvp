# src/cli.py
import json
from itertools import combinations

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


def print_intents(onos_response: dict) -> None:
    """
    Pretty-print the intents returned by ONOS /intents.
    """
    intents = onos_response.get("intents", []) or onos_response.get("entries", [])

    if not intents:
        print("No intents currently installed.")
        return

    print(f"Found {len(intents)} intents:")
    for intent in intents:
        intent_id = intent.get("id", "<no-id>")
        app_id = intent.get("appId", "<no-appId>")
        itype = intent.get("type", "<no-type>")
        one = intent.get("one", "<no-one>")
        two = intent.get("two", "<no-two>")
        priority = intent.get("priority", "<no-priority>")

        print(
            f"- id={intent_id}, appId={app_id}, type={itype}, "
            f"priority={priority}, one={one}, two={two}"
        )


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


def main():
    print("=== ONOS NL Intent CLI (MVP) ===")
    print("Supported (via LLM planner):")
    print("- 'connect h1 and h3'                    -> install HostToHostIntent")
    print("- 'connect h1, h2 and h3'                -> install intents for all pairs (mesh)")
    print("- 'list intents'                         -> list installed intents")
    print("- 'remove intents from h1 and h3'        -> delete intents involving BOTH hosts")
    print("- 'remove intents from h1'               -> delete all intents involving that host")
    print("- 'remove intents from h1, h2 and h3'    -> delete intents with BOTH endpoints in {h1,h2,h3}")
    print("- 'remove all intents'                   -> delete ALL intents for the ovsdb appId")
    print("Type 'exit' or 'quit' to leave.\n")

    while True:
        try:
            user_req = input(">> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not user_req:
            continue

        if user_req.lower() in {"exit", "quit"}:
            break

        # 1) Ask the LLM planner what to do
        try:
            planner_output = call_llm(
                system_prompt=PLANNER_SYSTEM_PROMPT,
                user_prompt=user_req,
                temperature=0.0,
            )
        except Exception as e:
            print(f"[ERROR] Failed to call LLM: {e}")
            continue

        # 2) Parse JSON plan (strip optional ```json fences)
        raw = _strip_markdown_fence(planner_output)
        try:
            plan = json.loads(raw)
        except json.JSONDecodeError:
            print("[ERROR] LLM did not return valid JSON. Raw output:")
            print(planner_output)
            continue

        operation = plan.get("operation")
        src_host = plan.get("src_host")
        dst_host = plan.get("dst_host")
        priority = plan.get("priority") or 55
        hosts_list = plan.get("hosts") or []

        print(
            f"[DEBUG] Plan: operation={operation}, "
            f"src_host={src_host}, dst_host={dst_host}, "
            f"priority={priority}, hosts={hosts_list}"
        )

        # 3) Dispatch based on operation
        if operation == "connect_hosts":
            # Multi-host mesh connect (>= 2 hosts)
            hosts = hosts_list
            if not hosts or not isinstance(hosts, list) or len(hosts) < 2:
                print(
                    "[ERROR] Planner chose 'connect_hosts' but did not provide a valid "
                    "hosts list with >= 2 items."
                )
                print(f"Plan: {plan}")
                continue

            created = []
            errors = []

            # Full mesh: create an intent for every unordered pair
            host_pairs = list(combinations(hosts, 2))
            for a, b in host_pairs:
                try:
                    resp = create_host_to_host_intent(a, b, priority)
                    created.append((a, b, resp))
                except Exception as e:
                    errors.append((a, b, str(e)))

            print("\n=== Intents Installed (Mesh) ===")
            print(f"Requested: {user_req}")
            print(f"Hosts: {hosts}")
            print(f"Priority: {priority}")
            print(f"Intents attempted: {len(host_pairs)}")
            print(f"Intents created: {len(created)}")

            if created:
                print("\nCreated:")
                for a, b, resp in created:
                    print(f"- {a} <-> {b} -> {resp}")

            if errors:
                print("\nErrors:")
                for a, b, err in errors:
                    print(f"- {a} <-> {b}: {err}")

            print("===============================\n")

        elif operation == "delete_intents_between_hosts":
            # Two possible modes:
            # 1) hosts_list has >= 2 items: delete intents whose endpoints are within that host set
            # 2) hosts_list is empty or len < 2: use src_host/dst_host semantics (1 or 2 hosts)
            if hosts_list and len(hosts_list) >= 2:
                # N-host deletion (within-set semantics)
                try:
                    matches = find_intents_within_host_set(
                        host_tokens=hosts_list,
                        app_id="org.onosproject.ovsdb",
                        intent_type="HostToHostIntent",
                    )
                except Exception as e:
                    print(
                        f"[ERROR] Failed while searching intents within host set {hosts_list}: {e}"
                    )
                    continue

                if not matches:
                    print(
                        f"No HostToHostIntents found with BOTH endpoints inside {hosts_list}."
                    )
                    continue

                deleted = []
                errors = []

                for intent in matches:
                    intent_id = intent.get("id") or intent.get("key")
                    app_id = intent.get("appId", "org.onosproject.ovsdb")

                    if not intent_id:
                        errors.append((app_id, "<missing-id>", "Intent missing id/key"))
                        continue

                    try:
                        resp = delete_intent(app_id, intent_id)
                        deleted.append((app_id, intent_id, resp, intent))
                    except Exception as e:
                        errors.append((app_id, intent_id, str(e)))

                print("\n=== Intent Deletion Result (within host set) ===")
                print(f"Requested: {user_req}")
                print(f"Host set: {hosts_list}")
                print(f"Matched intents: {len(matches)}")
                print(f"Deleted intents: {len(deleted)}")

                if deleted:
                    print("\nDeleted:")
                    for app_id, intent_id, resp, intent in deleted:
                        resources = intent.get("resources", [])
                        state = intent.get("state", "<no-state>")
                        print(
                            f"- {app_id}/{intent_id} resources={resources} "
                            f"state={state} -> {resp}"
                        )

                if errors:
                    print("\nErrors:")
                    for app_id, intent_id, err in errors:
                        print(f"- {app_id}/{intent_id}: {err}")

                print("===============================================\n")

            else:
                # Classic 1- or 2-host deletion
                if not src_host and not dst_host:
                    print(
                        "[ERROR] Planner chose delete_intents_between_hosts but "
                        "did not provide src_host/dst_host or a hosts list with >= 2 items."
                    )
                    print(f"Plan: {plan}")
                    continue

                host_tokens = [src_host] + ([dst_host] if dst_host else [])
                match_mode = "all" if dst_host else "any"

                try:
                    matches = find_intents_for_hosts(
                        host_tokens=host_tokens,
                        match_mode=match_mode,
                        app_id="org.onosproject.ovsdb",
                        intent_type="HostToHostIntent",
                    )
                except Exception as e:
                    print(
                        f"[ERROR] Failed while searching intents for {host_tokens}: {e}"
                    )
                    continue

                if not matches:
                    if dst_host:
                        print(
                            f"No HostToHostIntents found involving BOTH {src_host} and {dst_host}."
                        )
                    else:
                        print(f"No HostToHostIntents found involving {src_host}.")
                    continue

                deleted = []
                errors = []

                for intent in matches:
                    intent_id = intent.get("id") or intent.get("key")
                    app_id = intent.get("appId", "org.onosproject.ovsdb")

                    if not intent_id:
                        errors.append((app_id, "<missing-id>", "Intent missing id/key"))
                        continue

                    try:
                        resp = delete_intent(app_id, intent_id)
                        deleted.append((app_id, intent_id, resp, intent))
                    except Exception as e:
                        errors.append((app_id, intent_id, str(e)))

                print("\n=== Intent Deletion Result ===")
                print(f"Requested: {user_req}")
                print(
                    f"Deletion scope: {'BOTH hosts' if dst_host else 'ALL intents for host'}"
                )
                print(f"Matched intents: {len(matches)}")
                print(f"Deleted intents: {len(deleted)}")

                if deleted:
                    print("\nDeleted:")
                    for app_id, intent_id, resp, intent in deleted:
                        resources = intent.get("resources", [])
                        state = intent.get("state", "<no-state>")
                        print(
                            f"- {app_id}/{intent_id} resources={resources} "
                            f"state={state} -> {resp}"
                        )

                if errors:
                    print("\nErrors:")
                    for app_id, intent_id, err in errors:
                        print(f"- {app_id}/{intent_id}: {err}")

                print("==============================\n")

        elif operation == "delete_all_intents":
            # New operation: delete ALL intents for the ovsdb appId
            results = delete_all_intents(app_id="org.onosproject.ovsdb")

            total = len(results)
            deleted = sum(1 for r in results if r.get("ok"))
            errors = [r for r in results if not r.get("ok")]

            print("\n=== Delete All Intents Result ===")
            print(f"Requested: {user_req}")
            print(f"Total intents processed: {total}")
            print(f"Successfully deleted: {deleted}")

            if errors:
                print("\nErrors:")
                for r in errors:
                    intent = r.get("intent", {})
                    intent_id = r.get("id")
                    err = r.get("error")
                    resources = intent.get("resources", [])
                    state = intent.get("state", "<no-state>")
                    print(
                        f"- id={intent_id}, resources={resources}, state={state}, error={err}"
                    )

            print("================================\n")

        elif operation == "list_intents":
            try:
                onos_response = list_intents()
            except Exception as e:
                print(f"[ERROR] Failed to list intents: {e}")
                continue

            print("\n=== Current ONOS Intents ===")
            print_intents(onos_response)
            print("================================\n")

        else:
            print("Planner returned operation 'other' or an unknown operation.")
            print("This MVP currently only supports:")
            print("- connect_hosts (2 or more hosts, mesh)")
            print("- delete_intents_between_hosts (1, 2, or N hosts in 'hosts')")
            print("- delete_all_intents")
            print("- list_intents")
            print(f"Plan was: {plan}\n")


if __name__ == "__main__":
    main()
