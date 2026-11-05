"""Tiny client for a local Ollama server."""
import os

import requests


def int_env(name: str, default: int) -> int:
    """Read a positive whole-number setting, with a clear message instead of a traceback.

    Zero or less makes no sense for any of them (LAB_TIMEOUT=0 would even fail
    inside requests and look like a bad answer from Ollama).
    """
    value = os.environ.get(name, str(default))
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number < 1:
        raise SystemExit(f"{name} precisa ser um número inteiro maior que zero (recebi {value!r}).")
    return number


def chat_url(base: str) -> str:
    """A trailing slash in OLLAMA_URL would give "//api/chat", which Ollama answers with 404."""
    return base.rstrip("/") + "/api/chat"


OLLAMA_URL = chat_url(os.environ.get("OLLAMA_URL", "http://localhost:11434"))
MODEL = os.environ.get("LAB_MODEL", "gemma3:1b")
# Keep the PC usable while the model thinks: use only some CPU threads.
NUM_THREADS = int_env("LAB_THREADS", 2)
# Unload the model from memory after this much idle time.
KEEP_ALIVE = os.environ.get("LAB_KEEP_ALIVE", "1m")
# Context window in tokens. Ollama's default can be as low as 2048, and when
# the conversation does not fit it silently drops the START of the prompt,
# which is exactly where the secret and the security rules live.
NUM_CTX = int_env("LAB_CTX", 4096)
TIMEOUT = int_env("LAB_TIMEOUT", 300)  # seconds; small CPUs are slow
# Max tokens per answer: a rambling 1B model on a slow CPU could hit the timeout.
MAX_TOKENS = int_env("LAB_MAX_TOKENS", 256)


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
                "options": {"num_thread": NUM_THREADS, "num_ctx": NUM_CTX, "num_predict": MAX_TOKENS},
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        content = r.json()["message"]["content"]
        if not isinstance(content, str):
            raise TypeError("content is not text")
        return content
    except requests.ConnectionError:
        raise LLMError("O Ollama não está rodando. Abra o Ollama e tente de novo.")
    except requests.Timeout:
        raise LLMError(f"O modelo demorou mais de {TIMEOUT}s para responder. Tente de novo.")
    except requests.HTTPError:
        try:
            detail = str(r.json().get("error", ""))[:200]
        except (ValueError, AttributeError):
            detail = ""
        raise LLMError(f"Erro do Ollama: {detail or r.status_code} (você rodou `ollama pull {MODEL}`?)")
    # requests.JSONDecodeError is also a RequestException, but a body that is
    # not JSON is a bad answer, not a connection problem: catch it first.
    # (InvalidSchema/InvalidURL are ValueErrors too, so plain ValueError must
    # stay after RequestException.)
    except requests.JSONDecodeError:
        raise LLMError("O Ollama devolveu uma resposta inesperada.")
    except requests.RequestException as e:  # bad OLLAMA_URL, broken connection...
        raise LLMError(f"Não deu pra falar com o Ollama em {OLLAMA_URL}: {type(e).__name__}.")
    except (ValueError, KeyError, TypeError):
        raise LLMError("O Ollama devolveu uma resposta inesperada.")
