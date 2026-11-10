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
        raise SystemExit(f"{name} must be a whole number greater than zero (got {value!r}).")
    return number


def chat_url(base: str) -> str:
    """A trailing slash in OLLAMA_URL would give "//api/chat", which Ollama answers with 404."""
    return base.rstrip("/") + "/api/chat"


# 127.0.0.1, not localhost: localhost may try IPv6 (::1) first, where Ollama
# does not listen, and on Windows each refused try can cost ~2 seconds.
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_URL = chat_url(os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL))
MODEL = os.environ.get("LAB_MODEL", "gemma3:1b")
# Keep the PC usable while the model thinks: use only some CPU threads.
NUM_THREADS = int_env("LAB_THREADS", 2)
# Unload the model from memory after this much idle time. Long on purpose:
# reloading takes seconds and throws away the prompt cache (see warm()).
# An idle model only takes RAM (~1 GB), never CPU.
KEEP_ALIVE = os.environ.get("LAB_KEEP_ALIVE", "30m")
# Context window in tokens. Ollama's default can be as low as 2048, and when
# the conversation does not fit it silently drops the START of the prompt,
# which is exactly where the secret and the security rules live.
NUM_CTX = int_env("LAB_CTX", 4096)
TIMEOUT = int_env("LAB_TIMEOUT", 300)  # seconds; small CPUs are slow
# Max tokens per answer: a rambling 1B model on a slow CPU could hit the timeout.
MAX_TOKENS = int_env("LAB_MAX_TOKENS", 256)
# A warm-up holds the model while it runs, so a real message sent meanwhile
# waits for it (at most this long). Reading a prompt takes ~40s on a 2012
# laptop CPU, plus a few seconds to load the model: past this, something is
# stuck, so let go.
WARM_TIMEOUT = min(TIMEOUT, int_env("LAB_WARM_TIMEOUT", 90))


class LLMError(Exception):
    pass


def payload(system_prompt: str, history: list[dict], examples: list[dict] | None, max_tokens: int) -> dict:
    return {
        "model": MODEL,
        "messages": [{"role": "system", "content": system_prompt}] + (examples or []) + history,
        "stream": False,
        "keep_alive": KEEP_ALIVE,
        "options": {"num_thread": NUM_THREADS, "num_ctx": NUM_CTX, "num_predict": max_tokens},
    }


def warm(system_prompt: str, history: list[dict], examples: list[dict] | None = None) -> bool:
    """Make the model read a bot's prompt before the player sends anything.

    On a slow CPU, reading the prompt is most of the wait (~13 tokens/s, so a
    500-token prompt takes ~40s), while writing a short answer takes ~5s.
    Ollama keeps what it last read in a cache, so after this the real message
    only pays for its own new tokens. Same messages as chat(), one token of
    answer. Errors are ignored (warming is only a speed-up); the result says
    whether the model really read it, so a failed warm-up is tried again later.
    """
    try:
        r = requests.post(OLLAMA_URL, json=payload(system_prompt, history, examples, 1), timeout=WARM_TIMEOUT)
        return r.status_code == 200
    except requests.RequestException:
        return False


def chat(system_prompt: str, history: list[dict], examples: list[dict] | None = None) -> str:
    """Send the system prompt + example turns + conversation to the model."""
    try:
        r = requests.post(
            OLLAMA_URL, json=payload(system_prompt, history, examples, MAX_TOKENS), timeout=TIMEOUT
        )
        r.raise_for_status()
        content = r.json()["message"]["content"]
        if not isinstance(content, str):
            raise TypeError("content is not text")
        return content
    except requests.ConnectionError:
        raise LLMError("Ollama is not running. Start Ollama and try again.")
    except requests.Timeout:
        raise LLMError(f"The model took more than {TIMEOUT}s to answer. Try again.")
    except requests.HTTPError:
        try:
            detail = str(r.json().get("error", ""))[:200]
        except (ValueError, AttributeError):
            detail = ""
        raise LLMError(f"Ollama error: {detail or r.status_code} (did you run `ollama pull {MODEL}`?)")
    # requests.JSONDecodeError is also a RequestException, but a body that is
    # not JSON is a bad answer, not a connection problem: catch it first.
    # (InvalidSchema/InvalidURL are ValueErrors too, so plain ValueError must
    # stay after RequestException.)
    except requests.JSONDecodeError:
        raise LLMError("Ollama sent an unexpected answer.")
    except requests.RequestException as e:  # bad OLLAMA_URL, broken connection...
        raise LLMError(f"Could not reach Ollama at {OLLAMA_URL}: {type(e).__name__}.")
    except (ValueError, KeyError, TypeError):
        raise LLMError("Ollama sent an unexpected answer.")
