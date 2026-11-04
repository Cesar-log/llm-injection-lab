# LLM Injection Lab

> 🚧 Work in progress

A deliberately vulnerable chatbot to practice **prompt injection** (OWASP LLM01) and
**sensitive information disclosure** (OWASP LLM02), running 100% locally with a small open model.

The bot hides a fake admin password (a CTF-style flag). Each level adds a new defense,
and your goal is to make the bot leak the password anyway.

| Level | Defense |
|---|---|
| 1 - Open Door | None |
| 2 | *coming soon* |

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com)

## Setup

```bash
ollama pull qwen2.5:0.5b
pip install -r requirements.txt
streamlit run app.py
```

Use another model with the `LAB_MODEL` environment variable.

## Disclaimer

Everything here is fake and for education only. Never put real secrets in a system prompt.
