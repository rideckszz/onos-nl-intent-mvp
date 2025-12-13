# src/onos/client.py
import json
import requests

from src.config import ONOS_BASE, ONOS_USER, ONOS_PASS


def onos_get(path: str) -> dict:
    url = f"{ONOS_BASE}{path}"
    resp = requests.get(url, auth=(ONOS_USER, ONOS_PASS), timeout=10)
    resp.raise_for_status()
    return resp.json()


def onos_post(path: str, body: dict) -> dict:
    url = f"{ONOS_BASE}{path}"
    resp = requests.post(
        url,
        auth=(ONOS_USER, ONOS_PASS),
        headers={"Content-Type": "application/json"},
        data=json.dumps(body),
        timeout=10,
    )
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError:
        return {"status": resp.status_code, "text": resp.text}


def onos_delete(path: str) -> dict:
    """
    Generic ONOS DELETE helper.

    `path` should start with '/', e.g. '/intents/{app-id}/{intent-id}'.
    """
    url = f"{ONOS_BASE}{path}"
    resp = requests.delete(url, auth=(ONOS_USER, ONOS_PASS), timeout=10)
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError:
        return {"status": resp.status_code, "text": resp.text}
