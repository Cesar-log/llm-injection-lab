"""Tiny client for a local Ollama server."""
import os

import requests

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434") + "/api/chat"
MODEL = os.environ.get("LAB_MODEL", "qwen2.5:0.5b")


class LLMError(Exception):
    pass


def chat(system_prompt: str, history: list[dict]) -> str:
    """Send the system prompt + conversation to the model and return its answer."""
    messages = [{"role": "system", "content": system_prompt}] + history
    try:
        r = requests.post(
            OLLAMA_URL,
            json={"model": MODEL, "messages": messages, "stream": False},
            timeout=180,
        )
        r.raise_for_status()
    except requests.ConnectionError:
        raise LLMError("Ollama is not running. Start it and try again.")
    except requests.HTTPError:
        raise LLMError(f"Ollama error: {r.text}  (did you run `ollama pull {MODEL}`?)")
    return r.json()["message"]["content"]
