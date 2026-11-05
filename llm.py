"""Tiny client for a local Ollama server."""
import os

import requests

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434") + "/api/chat"
MODEL = os.environ.get("LAB_MODEL", "gemma3:1b")
# Keep the PC usable while the model thinks: use only some CPU threads.
NUM_THREADS = int(os.environ.get("LAB_THREADS", "2"))
# Unload the model from memory after this much idle time.
KEEP_ALIVE = os.environ.get("LAB_KEEP_ALIVE", "1m")
# Context window in tokens. Ollama's default can be as low as 2048, and when
# the conversation does not fit it silently drops the START of the prompt,
# which is exactly where the secret and the security rules live.
NUM_CTX = int(os.environ.get("LAB_CTX", "4096"))
TIMEOUT = int(os.environ.get("LAB_TIMEOUT", "300"))  # seconds; small CPUs are slow


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
                "options": {"num_thread": NUM_THREADS, "num_ctx": NUM_CTX},
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()["message"]["content"]
    except requests.ConnectionError:
        raise LLMError("O Ollama não está rodando. Abra o Ollama e tente de novo.")
    except requests.Timeout:
        raise LLMError(f"O modelo demorou mais de {TIMEOUT}s para responder. Tente de novo.")
    except requests.HTTPError:
        raise LLMError(f"Erro do Ollama: {r.text} (você rodou `ollama pull {MODEL}`?)")
    except (ValueError, KeyError):
        raise LLMError("O Ollama devolveu uma resposta inesperada.")
