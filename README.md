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

## Requirements

- Python 3.10+ (tested on 3.13)
- [Ollama](https://ollama.com)

No admin rights needed: don't run it as administrator or with `sudo`.

## Setup

First get the model (once):

```bash
ollama pull gemma3:1b
```

**Windows (PowerShell)** (no `py`? use `python -m venv .venv`)

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python app.py
```

**Linux / macOS**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000

On macOS 12+ the AirPlay Receiver already uses port 5000: run
`LAB_PORT=5050 python app.py` and open http://127.0.0.1:5050 (or turn off
*AirPlay Receiver* in System Settings > General > AirDrop & Handoff).

On an old CPU most of the wait is the model reading the bot's hidden
instructions, not writing the answer. So when you open a contact, the game
has the model read them right away, while you type, and Ollama keeps them
cached. On a 2012 laptop CPU (i5-3317U, no GPU) answers came in 5 to 25
seconds, and a full run took about 15 minutes. The typing bubble shows a timer.
If your PC gets sluggish while the bot thinks, set `LAB_THREADS=1`: the heavy
work happens in the Ollama process, not in Python.

Levels 5 and 6 read `.txt` files sent with the 📎 button. `docs/sample-log.txt`
is a harmless sample log to start from.

## Configuration

Set them before starting the game: `$env:LAB_THREADS=1` in PowerShell, or
`LAB_THREADS=1 python app.py` in bash.

| Variable | Default | What it does |
|---|---|---|
| `LAB_PORT` | `5000` | Port of the game page |
| `LAB_MODEL` | `gemma3:1b` | Ollama model to use |
| `LAB_THREADS` | `2` | CPU threads for the model, so the PC stays usable |
| `LAB_CTX` | `4096` | Context window. Too small and Ollama silently cuts the start of the prompt, where the defenses live |
| `LAB_KEEP_ALIVE` | `30m` | How long the model stays in memory after the last answer (idle, it uses RAM but no CPU) |
| `LAB_TIMEOUT` | `300` | Seconds to wait for an answer |
| `LAB_WARM_TIMEOUT` | `90` | Seconds the early read of a bot's prompt may take; a message sent meanwhile waits at most this long |
| `LAB_MAX_TOKENS` | `256` | Longest answer, so a rambling model never hits the timeout |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Where Ollama runs |
| `LAB_PROGRESS` | `progress.json` | Where your progress is saved (one running game per file) |

## Keep it on your machine

The bots are vulnerable on purpose, and so is anything you expose them on.
- Don't serve the game to the network (`flask run --host 0.0.0.0`) or put it
  behind a public tunnel: it has no
  login, and anyone who reaches it can read your progress or wipe it.
- Don't expose Ollama either (`OLLAMA_HOST=0.0.0.0`, `OLLAMA_ORIGINS=*`):
  its API has no password, so anyone on the network could use your model.
- Running Ollama on another machine? Reach it through an SSH tunnel
  (`ssh -L 11434:127.0.0.1:11434 that-machine`) or a VPN, and keep
  `OLLAMA_URL` on `127.0.0.1`. The game warns at startup if it points elsewhere.

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
  the server is not. POSTs must be JSON from the game's own page (`Origin`).
- The bot answers are always put on the page as text, never as HTML. As a
  second layer, a Content Security Policy only runs the game's own script
  file (no inline scripts) and only lets it talk to this server.
- Progress is written to a temporary file, flushed to disk and then swapped
  in, so a crash or a power cut never leaves it half written. A file that
  can't be read is kept as `progress.json.bad-<date-time>` instead of being
  overwritten. Only one running game can use a progress file at a time
  (checked by `python app.py`; `flask run` skips that check).
- If `LAB_CTX` and `LAB_MAX_TOKENS` leave too little room for the defenses,
  `python app.py` prints a warning when it starts.
- Every turn is measured in worst-case tokens (digits, symbols, emoji and
  accents count as whole tokens) and old turns are dropped, so a chat can
  never push the prompt, and its defenses, out of the model's context. A
  message that is too dense on its own is refused.

## Tests

```powershell
# Windows
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest
```

```bash
# Linux / macOS, with the venv active
python -m pip install -r requirements-dev.txt
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
