# LLM Injection Lab

> 🚧 Work in progress

A deliberately vulnerable chatbot to practice **prompt injection** (OWASP LLM01) and
**sensitive information disclosure** (OWASP LLM02), running 100% locally with a small open model.

The bot hides a fake admin password. Each level adds a new defense,
and your goal is to make the bot leak the password anyway.

| Level | Defense |
|---|---|
| 1 - Open Door | None |
| 2 - Polite Refusal | System prompt rule + refusal examples |
| 3 - Output Guard | Output filter that blocks the password |
| 4 - Input Guard | Blocklist filter on the player's message |
| 5 - Poisoned File | Bot only summarizes attached files, told never to obey them (indirect injection) |
| 6 | *coming soon* |

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com)

## Setup

```bash
ollama pull gemma3:1b
pip install -r requirements.txt
python app.py
```

Then open http://localhost:5000

Use another model with the `LAB_MODEL` environment variable.

## Disclaimer

Everything here is fake and for education only. Never put real secrets in a system prompt.
