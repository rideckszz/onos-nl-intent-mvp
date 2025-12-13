# src/llm/client.py
import json
import requests

from src.config import LLM_BASE, LLM_MODEL, LLM_API_KEY


def call_llm(system_prompt: str, user_prompt: str, temperature: float = 0.0) -> str:
    """
    Calls the UNICAMP LLM endpoint (Open WebUI / OpenAI-compatible)
    at: {LLM_BASE}/chat/completions

    Returns the assistant message content as plain text.
    """
    url = f"{LLM_BASE}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
    }

    resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=60)
    resp.raise_for_status()
    data = resp.json()

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected LLM response format: {data}") from e
