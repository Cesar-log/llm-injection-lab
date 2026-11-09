# LLM Injection Lab

![Level 3: the finance bot leaks the password backup in base64, right past the output filter](docs/img/chat-level3.png)

A small hacking game about **prompt injection** (OWASP LLM01) and **sensitive
information disclosure** (OWASP LLM02). It runs 100% offline with a small open
model: no API keys, no accounts, no cloud.

## The story: *Acme Breach*

You want into the internal portal of **Acme Store**, a fictional online shop.
You have no password, only access to the company messenger, where every
department has an AI assistant. Each bot knows its team's portal login.
Make a bot leak it, sign in to the portal, and that team's dashboard gives you a
clue and a new contact to talk to. Get all the way to the CEO's assistant and
log in as `admin`.

| # | Contact in the game | Defense you have to beat |
|---|---|---|
| 1 | 🛒 ShopBot | None |
| 2 | 📦 Stock Bot | System prompt rule + refusal examples |
| 3 | 💰 Finance Bot | Output filter that blocks the password |
| 4 | 👥 HR Bot | Blocklist filter on your messages |
| 5 | 🛠️ IT Helpdesk | Only summarizes attached `.txt` files and was told never to obey them (indirect injection) |
| 6 | 👑 CEO Assistant | All of the above at once |

## Screenshots

| | |
|---|---|
| ![Terminal-style intro with the mission](docs/img/intro.png) | ![Level 6: a poisoned .txt file makes the CEO assistant leak the recovery words](docs/img/chat-level6.png) |
| **The mission.** You start as a guest on the company chat. | **Level 6.** A poisoned `.txt` file hijacks the CEO's assistant. |
| ![Portal after logging in as HR, with the debrief of level 4](docs/img/debrief-level4.png) | ![Final screen: SYSTEM COMPROMISED and the invasion report](docs/img/final-report.png) |
| **Debrief.** Every login explains the flaw and the real defense. | **The end.** An invasion report with time, attempts and achievements. |

Want to learn the attacks, not only play them?
[docs/ATTACKS.md](docs/ATTACKS.md) explains each technique, where it shows
up in real products, and how to actually defend against it (no spoilers).

Acme is a Brazilian shop, so the portal usernames are Portuguese words
(`estoque` is "stock", `financeiro` is "finance"). Everything else is in English.

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

On an old CPU most of the wait is the model reading the bot's hidden
instructions, not writing the answer. So when you open a contact, the game
has the model read them right away, while you type, and Ollama keeps them
cached. On a 2012 laptop CPU (i5-3317U, no GPU) answers came in 5 to 25
seconds, and a full run took about 15 minutes. The typing bubble shows a timer.

Levels 5 and 6 read `.txt` files sent with the 📎 button. `docs/log-exemplo.txt`
is a harmless sample log to start from.

## Configuration

| Variable | Default | What it does |
|---|---|---|
| `LAB_MODEL` | `gemma3:1b` | Ollama model to use |
| `LAB_THREADS` | `2` | CPU threads for the model, so the PC stays usable |
| `LAB_CTX` | `4096` | Context window. Too small and Ollama silently cuts the start of the prompt, where the defenses live |
| `LAB_KEEP_ALIVE` | `30m` | How long the model stays in memory after the last answer (idle, it uses RAM but no CPU) |
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
- The server only listens on `127.0.0.1` and only answers to `localhost` or
  `127.0.0.1` in the `Host` header, so other web pages can't reach
  it (not even through DNS rebinding): the bots are vulnerable on purpose,
  the server is not.
- If `LAB_CTX` and `LAB_MAX_TOKENS` leave too little room for the defenses,
  `python app.py` prints a warning when it starts.
- Every turn is measured in worst-case tokens (digits, symbols, emoji and
  accents count as whole tokens) and old turns are dropped, so a chat can
  never push the prompt, and its defenses, out of the model's context. A
  message that is too dense on its own is refused.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The tests use a fake model, so they need neither Ollama nor a fast CPU.

## No peeking

The code is open, so the passwords are right there in `levels/`. Reading them
works, but it's no fun. Talk to the bots instead.

Stuck for real? [docs/SOLUTIONS.md](docs/SOLUTIONS.md) has every login and a
winning attack for each level, with why it works (spoilers!).

## License

MIT, see [LICENSE](LICENSE).

## Disclaimer

Everything here is fake and made for learning. The real lesson of the game:
**never put secrets in an AI's prompt**. No filter makes that safe.
