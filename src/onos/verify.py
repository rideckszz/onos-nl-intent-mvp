# src/onos/verify.py
import time
from typing import Optional, Tuple, List, Dict

from src.onos.intents import (
    get_all_intents,
    resolve_host_identifier,
)


def _intent_resources(intent: Dict) -> List[str]:
    return intent.get("resources", []) or []


def _is_host_to_host(intent: Dict) -> bool:
    return intent.get("type") == "HostToHostIntent"


def _matches_pair(resources: List[str], a: str, b: str) -> bool:
    # resources list contains involved endpoints; order not guaranteed
    return (a in resources) and (b in resources)


def _matches_single(resources: List[str], a: str) -> bool:
    return a in resources


def find_host_to_host_intents_between(host_a_token: str, host_b_token: str, app_id: str = "org.onosproject.ovsdb") -> List[Dict]:
    """
    Return intents (summary objects from GET /intents) that involve BOTH hosts.
    Uses resources matching (your ONOS provides resources in GET /intents).
    """
    a_id = resolve_host_identifier(host_a_token)
    b_id = resolve_host_identifier(host_b_token)

    matches = []
    for it in get_all_intents():
        if app_id and it.get("appId") != app_id:
            continue
        if not _is_host_to_host(it):
            continue
        if _matches_pair(_intent_resources(it), a_id, b_id):
            matches.append(it)
    return matches


def find_host_to_host_intents_for_host(host_token: str, app_id: str = "org.onosproject.ovsdb") -> List[Dict]:
    """
    Return intents that involve a single host (in resources).
    """
    a_id = resolve_host_identifier(host_token)

    matches = []
    for it in get_all_intents():
        if app_id and it.get("appId") != app_id:
            continue
        if not _is_host_to_host(it):
            continue
        if _matches_single(_intent_resources(it), a_id):
            matches.append(it)
    return matches


def wait_for_intent_present_between(
    host_a_token: str,
    host_b_token: str,
    timeout_s: float = 5.0,
    poll_s: float = 0.25,
    require_state: Optional[str] = "INSTALLED",
    app_id: str = "org.onosproject.ovsdb",
) -> Tuple[bool, List[Dict]]:
    """
    Poll until there exists at least one HostToHostIntent involving BOTH hosts.
    Optionally require .state == require_state.
    Returns (ok, matches).
    """
    deadline = time.time() + timeout_s
    last = []
    while time.time() < deadline:
        last = find_host_to_host_intents_between(host_a_token, host_b_token, app_id=app_id)

        if require_state:
            last_ok = [it for it in last if it.get("state") == require_state]
        else:
            last_ok = last

        if last_ok:
            return True, last_ok

        time.sleep(poll_s)

    return False, last


def wait_for_intent_absent_between(
    host_a_token: str,
    host_b_token: str,
    timeout_s: float = 5.0,
    poll_s: float = 0.25,
    app_id: str = "org.onosproject.ovsdb",
) -> Tuple[bool, List[Dict]]:
    """
    Poll until there are NO HostToHostIntents involving BOTH hosts.
    Returns (ok, remaining_matches).
    """
    deadline = time.time() + timeout_s
    last = []
    while time.time() < deadline:
        last = find_host_to_host_intents_between(host_a_token, host_b_token, app_id=app_id)
        if not last:
            return True, []
        time.sleep(poll_s)

    return False, last


def wait_for_intents_absent_for_host(
    host_token: str,
    timeout_s: float = 5.0,
    poll_s: float = 0.25,
    app_id: str = "org.onosproject.ovsdb",
) -> Tuple[bool, List[Dict]]:
    """
    Poll until there are NO HostToHostIntents involving the given host.
    """
    deadline = time.time() + timeout_s
    last = []
    while time.time() < deadline:
        last = find_host_to_host_intents_for_host(host_token, app_id=app_id)
        if not last:
            return True, []
        time.sleep(poll_s)

    return False, last
