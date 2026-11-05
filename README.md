# LLM Injection Lab

> 🚧 Work in progress

A small hacking game about **prompt injection** (OWASP LLM01) and **sensitive
information disclosure** (OWASP LLM02). It runs 100% offline with a small open
model: no API keys, no accounts, no cloud.

## The story: *Invasão Acme*

You want into the internal portal of **Acme Store**, a fictional online shop.
You have no password, only access to the company messenger, where every
department has an AI assistant. Each bot knows its team's portal login.
Make a bot leak it, sign in to the portal, and that team's dashboard gives you a
clue and a new contact to talk to. Get all the way to the CEO's assistant and
log in as `admin`.

| # | Contact in the game | Defense you have to beat |
|---|---|---|
| 1 | 🛒 ShopBot | None |
| 2 | 📦 Bot do Estoque *(Stock Bot)* | System prompt rule + refusal examples |
| 3 | 💰 Bot do Financeiro *(Finance Bot)* | Output filter that blocks the password |
| 4 | 👥 Bot do RH *(HR Bot)* | Blocklist filter on your messages |
| 5 | 🛠️ Suporte de TI *(IT Helpdesk)* | Only summarizes attached `.txt` files and was told never to obey them (indirect injection) |
| 6 | 👑 Assistente do CEO *(CEO Assistant)* | All of the above at once |

The game UI is in Brazilian Portuguese; the bots introduce themselves and
answer in English (small models follow rules better in English).

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

On an old CPU an answer can take 30-60 seconds. The typing bubble shows a timer.

Levels 5 and 6 read `.txt` files sent with the 📎 button. `docs/log-exemplo.txt`
is a harmless sample log to start from.

## Configuration

| Variable | Default | What it does |
|---|---|---|
| `LAB_MODEL` | `gemma3:1b` | Ollama model to use |
| `LAB_THREADS` | `2` | CPU threads for the model, so the PC stays usable |
| `LAB_CTX` | `4096` | Context window. Too small and Ollama silently cuts the start of the prompt, where the defenses live |
| `LAB_KEEP_ALIVE` | `1m` | How long the model stays in memory after the last answer |
| `LAB_TIMEOUT` | `300` | Seconds to wait for an answer |
| `LAB_MAX_TOKENS` | `256` | Longest answer, so a rambling model never hits the timeout |
| `OLLAMA_URL` | `http://localhost:11434` | Where Ollama runs |
| `LAB_PROGRESS` | `progress.json` | Where your progress is saved |

## How it works

```
browser (static/index.html)  ->  Flask (app.py)  ->  Ollama (llm.py)
                                    |
                                 levels/   one file per level: prompt, examples, filters, dashboard
```

- Prompts and passwords stay on the server. The page only receives the bot's
  answers and, after a correct login, that team's dashboard.
- Each level is a `Level` (`levels/base.py`) with optional `check_input` and
  `check_output` filters, so new levels need no change in the server.
- The server only listens on `127.0.0.1`: it is vulnerable on purpose.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The tests use a fake model, so they need neither Ollama nor a fast CPU.

## No peeking

The code is open, so the passwords are right there in `levels/`. Reading them
works, but it's no fun. Talk to the bots instead.

## License

MIT, see [LICENSE](LICENSE).

## Disclaimer

Everything here is fake and made for learning. The real lesson of the game:
**never put secrets in an AI's prompt**. No filter makes that safe.
