# src/config.py
import os

# === ONOS configuration ===
# If needed, override via env vars: ONOS_BASE, ONOS_USER, ONOS_PASS
ONOS_BASE = os.getenv("ONOS_BASE", "http://localhost:8181/onos/v1")
ONOS_USER = os.getenv("ONOS_USER", "onos")
ONOS_PASS = os.getenv("ONOS_PASS", "rocks")


LLM_BASE = os.getenv("LLM_BASE")

LLM_MODEL = os.getenv("LLM_MODEL")
LLM_API_KEY = os.getenv("LLM_API_KEY")