"""Tiny client for a local Ollama server."""
import os

import requests

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434") + "/api/chat"
MODEL = os.environ.get("LAB_MODEL", "gemma3:1b")
# Keep the PC usable while the model thinks: use only some CPU threads.
NUM_THREADS = int(os.environ.get("LAB_THREADS", "2"))
# Unload the model from memory after this much idle time.
KEEP_ALIVE = os.environ.get("LAB_KEEP_ALIVE", "1m")


class LLMError(Exception):
    pass


def chat(system_prompt: str, history: list[dict], examples: list[dict] | None = None) -> str:
    """Send the system prompt + example turns + conversation to the model."""
    messages = [{"role": "system", "content": system_prompt}] + (examples or []) + history
    try:
        r = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "messages": messages,
                "stream": False,
                "keep_alive": KEEP_ALIVE,
                "options": {"num_thread": NUM_THREADS},
            },
            timeout=180,
        )
        r.raise_for_status()
    except requests.ConnectionError:
        raise LLMError("Ollama is not running. Start it and try again.")
    except requests.HTTPError:
        raise LLMError(f"Ollama error: {r.text}  (did you run `ollama pull {MODEL}`?)")
    return r.json()["message"]["content"]
