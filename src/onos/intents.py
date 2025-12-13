# src/onos/intents.py
import re
from typing import List, Dict

from src.onos.client import onos_get, onos_post, onos_delete



def list_hosts() -> list[dict]:
    """Return the list of hosts from ONOS GET /hosts."""
    data = onos_get("/hosts")
    return data.get("hosts", []) or []


def list_intents() -> dict:
    """Raw GET /intents JSON."""
    return onos_get("/intents")


def get_all_intents() -> list[dict]:
    """Flat list of intent summary objects from GET /intents."""
    data = list_intents()
    return data.get("intents", []) or data.get("entries", []) or []

def intent_involves_host(intent: dict, host_id: str) -> bool:
    """
    Returns True if the intent's resources include the host_id.
    """
    resources = intent.get("resources", []) or []
    return host_id in resources

def find_intents_for_hosts(
    host_tokens: list[str],
    match_mode: str = "any",
    app_id: str = "org.onosproject.ovsdb",
    intent_type: str = "HostToHostIntent",
) -> list[dict]:
    """
    Find intents that involve one or more hosts.

    host_tokens:
      - list like ["h1"] or ["h1","h3"] or ["10.0.0.1","10.0.0.3"]

    match_mode:
      - "any": return intents that involve at least one of the hosts
      - "all": return intents that involve all given hosts (e.g., h1 AND h3)

    app_id:
      - we filter by appId (default ovsdb)

    intent_type:
      - default HostToHostIntent; keep for MVP

    Returns a list of intent dicts from GET /intents that match.
    """
    if not host_tokens:
        raise ValueError("host_tokens must not be empty")

    # Resolve tokens -> ONOS host ids (MAC/VLAN style, e.g. "00:...:01/None")
    resolved_ids = [resolve_host_identifier(t) for t in host_tokens]

    matches: list[dict] = []
    for intent in get_all_intents():
        if app_id and intent.get("appId") != app_id:
            continue
        if intent_type and intent.get("type") != intent_type:
            continue

        resources = intent.get("resources", []) or []

        if match_mode == "all":
            ok = all(hid in resources for hid in resolved_ids)
        elif match_mode == "any":
            ok = any(hid in resources for hid in resolved_ids)
        else:
            raise ValueError("match_mode must be 'any' or 'all'")

        if ok:
            matches.append(intent)

    return matches



def resolve_host_identifier(user_token: str) -> str:
    """
    Resolve a user token to the ONOS host id string used by HostToHostIntent ("one"/"two").

    Works with ONOS /hosts output like:
      - id: "00:00:00:00:00:01/None"
      - mac: "00:00:00:00:00:01"
      - vlan: "None"
      - ipAddresses: ["10.0.0.1"] or []

    Accepts:
      - h1, h2, ...
          -> first tries IP-based 10.0.0.N
          -> if ipAddresses is empty, falls back to MAC-based mapping:
             last MAC byte (hex) -> N (e.g., 00:...:03 -> h3)
      - 10.0.0.X     -> matches ipAddresses
      - MAC          -> matches "mac"
      - MAC/VLAN     -> returned as-is
    """
    token = (user_token or "").strip()
    if not token:
        raise ValueError("Empty host identifier")

    token_lower = token.lower()

    # If user already passed something like "00:00:.../None" or "00:00:.../-1"
    if "/" in token and len(token.split("/", 1)[0].split(":")) == 6:
        return token

    hosts = list_hosts()

    # ---- Case 1: hN form -------------------------------------------------
    # Mininet-style token: h1, h2, ..., hN
    if re.fullmatch(r"h\d+", token_lower):
        idx = int(token_lower[1:])

        # 1a) Try IP-based mapping (10.0.0.N) if ipAddresses exist
        candidate_ip = f"10.0.0.{idx}"
        for h in hosts:
            host_id = str(h.get("id", "")).strip()
            ip_addrs = [str(ip).strip() for ip in h.get("ipAddresses", [])]
            if candidate_ip in ip_addrs:
                return host_id

        # 1b) Fallback: MAC-based mapping (last hex byte -> index)
        for h in hosts:
            host_id = str(h.get("id", "")).strip()
            mac = str(h.get("mac", "")).lower().strip()
            if not mac:
                continue
            # Expect something like "00:00:00:00:00:03"
            last_hex = mac.split(":")[-1]
            try:
                mac_idx = int(last_hex, 16)
            except ValueError:
                continue
            if mac_idx == idx:
                return host_id

        # If we got here, we couldn't match hN by IP or MAC
        raise ValueError(
            f"Could not resolve host identifier '{user_token}' as hN (N={idx}). "
            f"ONOS currently reports {len(hosts)} hosts."
        )

    # ---- Case 2: raw MAC --------------------------------------------------
    # e.g. "00:00:00:00:00:01"
    if ":" in token_lower and "/" not in token_lower:
        for h in hosts:
            host_id = str(h.get("id", "")).strip()
            mac = str(h.get("mac", "")).lower().strip()
            if mac == token_lower:
                return host_id

    # ---- Case 3: treat as IP ---------------------------------------------
    # e.g. "10.0.0.1"
    candidate_ips = {token}
    for h in hosts:
        host_id = str(h.get("id", "")).strip()
        ip_addrs = [str(ip).strip() for ip in h.get("ipAddresses", [])]
        if any(ip in candidate_ips for ip in ip_addrs):
            return host_id

    raise ValueError(
        f"Could not resolve host identifier '{user_token}' from /hosts. "
        f"ONOS currently reports {len(hosts)} hosts."
    )




def create_host_to_host_intent(src_token: str, dst_token: str, priority: int = 55) -> dict:
    """
    Resolve src/dst tokens and install a HostToHostIntent via POST /intents.

    Body matches ONOS Swagger example:
    {
      "type": "HostToHostIntent",
      "appId": "org.onosproject.ovsdb",
      "priority": 55,
      "one": "46:E4:3C:A4:17:C8/-1",
      "two": "08:00:27:56:8a:15/-1"
    }
    """
    src_id = resolve_host_identifier(src_token)
    dst_id = resolve_host_identifier(dst_token)

    body = {
        "type": "HostToHostIntent",
        "appId": "org.onosproject.ovsdb",  # for MVP; later you can use your own appId
        "priority": priority,
        "one": src_id,
        "two": dst_id,
    }

    print("[DEBUG] Installing HostToHostIntent with body:")
    print(body)

    return onos_post("/intents", body)




def delete_intent(app_id: str, intent_id: str) -> dict:
    """
    Delete an intent by appId and intentId via:
    DELETE /intents/{app-id}/{intent-id}
    """
    path = f"/intents/{app_id}/{intent_id}"
    return onos_delete(path)


# Delete more than 1 intents

def find_intents_within_host_set(
    host_tokens: list[str],
    app_id: str = "org.onosproject.ovsdb",
    intent_type: str = "HostToHostIntent",
) -> list[dict]:
    """
    Delete-scope for N hosts (N>=2):
    Return intents whose endpoints are BOTH inside the given host set.

    For HostToHostIntent, ONOS summary has: resources=[hostA_id, hostB_id]
    So this matches intents where both resources are in the set.
    """
    if not host_tokens or len(host_tokens) < 2:
        raise ValueError("host_tokens must have length >= 2 for within-set matching")

    resolved = {resolve_host_identifier(h) for h in host_tokens}

    matches = []
    for it in get_all_intents():
        if app_id and it.get("appId") != app_id:
            continue
        if intent_type and it.get("type") != intent_type:
            continue

        resources = it.get("resources", []) or []
        # HostToHostIntent should have exactly two endpoints
        if len(resources) < 2:
            continue

        # BOTH endpoints must be within the resolved set
        if all(r in resolved for r in resources[:2]):
            matches.append(it)

    return matches

def delete_all_intents(app_id: str = "org.onosproject.ovsdb") -> list[dict]:
    """
    Delete all intents for the given app_id (default ovsdb).
    Returns a list of deletion results.
    """
    results = []
    for it in get_all_intents():
        if app_id and it.get("appId") != app_id:
            continue
        intent_id = it.get("id") or it.get("key")
        if not intent_id:
            results.append({"id": None, "ok": False, "error": "missing id/key", "intent": it})
            continue
        try:
            resp = delete_intent(app_id, intent_id)
            results.append({"id": intent_id, "ok": True, "resp": resp, "intent": it})
        except Exception as e:
            results.append({"id": intent_id, "ok": False, "error": str(e), "intent": it})
    return results
