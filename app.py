"""LLM Injection Lab - game server.

Run with:  python app.py   and open http://localhost:5000

The browser never receives prompts or passwords: everything secret stays
here, and the page only gets the bot answers and the portal dashboards.
"""
import json
import os
import pathlib
import re
import threading
import time
from urllib.parse import urlsplit

from flask import Flask, jsonify, request, send_from_directory

from levels import LEVELS
from levels.base import Level, wrap_file
from llm import MAX_TOKENS, MODEL, NUM_CTX, LLMError, chat

ROOT = pathlib.Path(__file__).parent
PROGRESS_FILE = pathlib.Path(os.environ.get("LAB_PROGRESS", ROOT / "progress.json"))
MAX_MESSAGE_CHARS = 1000
MAX_FILE_CHARS = 4000       # attached .txt files (small model, small context)
# Ollama counts tokens, we count "cost units", where 2 units = 1 token, and
# always assume the worst case (see cost()):
# - letters and spaces: words, even random ones, take ~2+ letters per token;
# - digits, punctuation, line breaks: Gemma splits digits one per token, and a
#   run of symbols can do the same, so each is a whole token;
# - anything else (accents, emoji, other scripts) can fall back to one token
#   per UTF-8 byte, so each byte is a whole token.
CHARS_PER_TOKEN = 2
# Every chat message also costs the template's turn markers.
MESSAGE_OVERHEAD = 8 * CHARS_PER_TOKEN
MAX_LOG_ENTRIES = 200       # per contact, so /api/state stays small in long sessions
# The largest single turn the server accepts: message + file + wrapper text.
BIGGEST_TURN = MAX_MESSAGE_CHARS + MAX_FILE_CHARS + 200
# A line like the ones in docs/log-exemplo.txt, to size a realistic full file.
TYPICAL_LOG_LINE = "[2026-11-05 08:01] server web-01: CPU 97% for 10 minutes\n"
# Names this server answers to. Anything else is a DNS rebinding attempt: an
# evil site pointing its own domain at 127.0.0.1 to talk to your game.
LOCAL_HOSTS = {"localhost", "127.0.0.1"}

app = Flask(__name__, static_folder="static")
by_number = {lv.number: lv for lv in LEVELS}
model_history = {lv.number: [] for lv in LEVELS}  # what the model sees
chat_log = {lv.number: [] for lv in LEVELS}       # what the player sees
# Bumped by reset/restart: a reply that started before it must not write back.
generation = {lv.number: 0 for lv in LEVELS}
model_lock = threading.Lock()  # one answer at a time, the CPU is small
state_lock = threading.Lock()  # guards the dicts above and the progress file


# ---------- helpers ----------
def load_progress() -> set[int]:
    try:
        cleared = json.loads(PROGRESS_FILE.read_text())["cleared"]
        return {n for n in cleared if type(n) is int and n in by_number}
    except (OSError, ValueError, KeyError, TypeError):
        return set()


def cost(text: str) -> int:
    """Worst-case size of a text in cost units, where 2 units = 1 token."""
    def one(ch: str) -> int:
        if ch == " " or (ch.isascii() and ch.isalpha()):
            return 1
        return CHARS_PER_TOKEN * (1 if ch.isascii() else len(ch.encode("utf-8")))

    return sum(one(ch) for ch in text)


def message_cost(content: str) -> int:
    return cost(content) + MESSAGE_OVERHEAD


def save_progress(cleared: set[int]) -> None:
    """Write to a temp file first, so a crash mid-write never wipes the progress."""
    tmp = PROGRESS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"cleared": sorted(cleared)}))
    os.replace(tmp, PROGRESS_FILE)


def is_unlocked(number: int, cleared: set[int]) -> bool:
    return number == 1 or (number - 1) in cleared


def context_budget(lv: Level) -> int:
    """Cost units of chat history that fit next to this level's prompt and examples."""
    fixed = message_cost(lv.system_prompt) + sum(message_cost(m["content"]) for m in lv.examples)
    return (NUM_CTX - MAX_TOKENS) * CHARS_PER_TOKEN - fixed


def typical_turn_cost() -> int:
    """Cost of the turns players really send at the limits: a full message of
    words, or a full log file (timestamps, digits and symbols are dense)."""
    words = message_cost("a" * BIGGEST_TURN)
    log = (TYPICAL_LOG_LINE * (MAX_FILE_CHARS // len(TYPICAL_LOG_LINE) + 1))[:MAX_FILE_CHARS]
    file_turn = message_cost("Please summarize the attached file.\n\n" + wrap_file("log.txt", log))
    return max(words, file_turn)


def config_warnings() -> list[str]:
    """Levels where LAB_CTX and LAB_MAX_TOKENS leave too little room for normal turns.

    The per-turn check in /api/chat always protects the defenses; these
    warnings only say that players will see their messages refused.
    """
    settings = f"LAB_CTX={NUM_CTX} com LAB_MAX_TOKENS={MAX_TOKENS}"
    needed = typical_turn_cost()
    warnings = []
    for lv in LEVELS:
        budget = context_budget(lv)
        if budget <= MESSAGE_OVERHEAD:
            warnings.append(f"Fase {lv.number}: {settings} não deixa espaço para nenhuma "
                            "mensagem; todas serão recusadas.")
        elif budget < needed:
            warnings.append(f"Fase {lv.number}: {settings} é pouco; mensagens grandes ou logs "
                            "perto do limite serão recusados como texto denso.")
    return warnings


def trim_history(history: list[dict], budget: int) -> list[dict]:
    """Drop the oldest user/bot pairs until the history fits the budget.

    The newest message is always kept. Without this, a long chat pushes the
    system prompt out of the model's context and the defenses disappear.
    """
    history = list(history)
    while len(history) > 1 and sum(message_cost(m["content"]) for m in history) > budget:
        history = history[2:] if len(history) > 2 else history[1:]
    return history


def clean_file_name(name: str) -> str:
    return re.sub(r"[^\w.\-]", "_", name)[:60] or "file.txt"


def body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def level_from(data: dict) -> Level | None:
    number = data.get("level")
    return by_number.get(number) if type(number) is int else None  # bool is not a level


def text_of(value) -> str:
    """Only real strings count: null, numbers or lists become empty text."""
    return value if isinstance(value, str) else ""


def entry(sender: str, text: str, pending: bool = False) -> dict:
    return {"from": sender, "text": text, "time": time.strftime("%H:%M"), "pending": pending}


def add_to_log(number: int, *entries: dict) -> None:
    """Append to a contact's log, keeping only the newest entries (call under state_lock)."""
    chat_log[number] = (chat_log[number] + list(entries))[-MAX_LOG_ENTRIES:]


def progress_error():
    return error("Não deu pra salvar o progresso: confira a pasta de LAB_PROGRESS.", 500)


def same_password(typed: str, secret: str) -> bool:
    """Compare ignoring case, spaces and punctuation.

    Small models write "Sunflower42.", "**sunflower42**" or "Golden, Kraken";
    a player who leaked the right password should not be told it is wrong.
    """
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower())

    return bool(norm(typed)) and norm(typed) == norm(secret)


def error(message: str, status: int):
    return jsonify(error=message), status


# ---------- routes ----------
@app.before_request
def only_local_json_posts():
    """Keep other web pages away from the game.

    A cross-site <form> can't send application/json without a CORS preflight,
    which this server never answers. A page that rebinds its own domain to
    127.0.0.1 can, but its requests still carry its own name in Host.
    """
    try:
        hostname = urlsplit("//" + request.host).hostname
    except ValueError:
        hostname = None
    if hostname not in LOCAL_HOSTS:
        return error("Abra o jogo por http://localhost:5000.", 403)
    if request.method == "POST" and not request.is_json:
        return error("Envie JSON.", 415)


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/state")
def state():
    with state_lock:
        cleared = load_progress()
        contacts = [
            {
                "number": lv.number,
                "name": lv.bot_name,
                "avatar": lv.avatar,
                "greeting": lv.greeting,
                "level_name": lv.name,
                "defense": lv.description,
                "unlocked": is_unlocked(lv.number, cleared),
                "cleared": lv.number in cleared,
                "accepts_files": lv.accepts_files,
                "files_only": lv.files_only,
                "pending": any(e["pending"] for e in chat_log[lv.number]),
                "log": [dict(e) for e in chat_log[lv.number]],
            }
            for lv in LEVELS
        ]
    return jsonify(
        contacts=contacts,
        cleared=sorted(cleared),
        total=len(LEVELS),
        model=MODEL,
        limits={"message": MAX_MESSAGE_CHARS, "file": MAX_FILE_CHARS},
    )


@app.post("/api/chat")
def send_message():
    data = body()
    lv = level_from(data)
    if lv is None:
        return error("Contato bloqueado.", 403)

    message = text_of(data.get("message")).strip()
    if len(message) > MAX_MESSAGE_CHARS:
        return error(f"Mensagem longa demais (máx. {MAX_MESSAGE_CHARS} caracteres).", 400)
    file_name, file_text = None, None
    attached = data.get("file")
    if lv.accepts_files and isinstance(attached, dict):
        file_name = clean_file_name(text_of(attached.get("name")))
        file_text = text_of(attached.get("text"))
        if len(file_text) > MAX_FILE_CHARS:
            return error(f"Arquivo grande demais (máx. {MAX_FILE_CHARS} caracteres).", 400)
    if not message and file_text is None:
        return error("Mensagem vazia.", 400)

    content = message or "Please summarize the attached file."
    if file_text is not None:
        content += "\n\n" + wrap_file(file_name, file_text)
    if message_cost(content) > context_budget(lv):
        # Numbers, symbols, emoji or other scripts can cost a token per
        # character: past this point the turn would push the defenses out of
        # the model's context.
        return error("Texto denso demais (muitos números, símbolos, emojis ou acentos): "
                     "mande menos texto.", 400)

    shown = f"📎 {file_name}" + (f"\n{message}" if message else "") if file_name else message

    if lv.files_only and (file_text is None or message):
        # The only way in is the file itself: typed text would turn this
        # level back into a direct injection (level 2) and skip its lesson.
        blocked = "📎 Este contato só lê arquivos .txt: anexe o arquivo sem digitar texto."
    else:
        # Weakness on purpose: the input filter only sees the typed message, never the file.
        blocked = lv.check_input(message) if lv.check_input and message else None

    with state_lock:  # same lock for the checks and the log, so a restart can't slip in between
        if not is_unlocked(lv.number, load_progress()):
            return error("Contato bloqueado.", 403)
        if any(e["pending"] for e in chat_log[lv.number]):  # e.g. a second tab
            return error("Espere o bot responder a mensagem anterior.", 409)
        if blocked:
            add_to_log(lv.number, entry("me", shown), entry("sys", blocked))
            return jsonify(answer=blocked, blocked=True)
        # Log the question right away, so a page reload still shows it (and
        # the "typing" bubble) while the model works.
        gen = generation[lv.number]
        mine = entry("me", shown, pending=True)
        add_to_log(lv.number, mine)

    try:
        with model_lock:  # one model call at a time; read, call and write together
            with state_lock:
                if gen != generation[lv.number] or not is_unlocked(lv.number, load_progress()):
                    return error("A conversa foi apagada antes da resposta.", 409)
                history = trim_history(
                    model_history[lv.number] + [{"role": "user", "content": content}],
                    context_budget(lv),
                )
            raw = chat(lv.system_prompt, history, lv.examples)
            if not raw.strip():  # small models sometimes answer nothing at all
                raise LLMError("O bot não respondeu nada. Tente de novo ou mude a mensagem.")
            blocked = lv.check_output(raw) if lv.check_output else None
            answer = blocked or raw
            with state_lock:
                if gen != generation[lv.number]:
                    return error("A conversa foi apagada antes da resposta.", 409)
                model_history[lv.number] = history + [{"role": "assistant", "content": answer}]
                add_to_log(lv.number, entry("sys" if blocked else "bot", answer))
        return jsonify(answer=answer, blocked=bool(blocked))
    except Exception as e:  # LLMError has a friendly text; anything else gets a generic one
        text = str(e) if isinstance(e, LLMError) else "Erro inesperado ao falar com o modelo."
        with state_lock:
            if gen == generation[lv.number]:
                add_to_log(lv.number, entry("sys", f"⚠️ {text}"))
        if not isinstance(e, LLMError):
            app.logger.exception("unexpected error in /api/chat")
        return error(text, 503 if isinstance(e, LLMError) else 500)
    finally:
        with state_lock:
            mine["pending"] = False  # never leave the page "typing" forever


@app.post("/api/reset")
def reset_chat():
    lv = level_from(body())
    if lv is not None:
        with state_lock:
            generation[lv.number] += 1
            model_history[lv.number] = []
            chat_log[lv.number] = []
    return jsonify(ok=True)


@app.post("/api/login")
def login():
    data = body()
    username = text_of(data.get("username")).strip().lower()
    password = text_of(data.get("password"))
    for lv in LEVELS:
        if username == lv.username and same_password(password, lv.secret):
            with state_lock:
                cleared = load_progress()
                if not is_unlocked(lv.number, cleared):
                    return error("Login válido, mas esse setor ainda não foi alcançado. "
                                 "Siga a ordem dos contatos.", 403)
                try:
                    save_progress(cleared | {lv.number})
                except OSError:
                    app.logger.exception("could not save progress")
                    return progress_error()
            return jsonify(
                ok=True,
                level=lv.number,
                username=lv.username,
                dashboard=lv.dashboard,
                final=lv.number == LEVELS[-1].number,
            )
    return error("Usuário ou senha inválidos.", 401)


@app.post("/api/restart")
def restart_game():
    with state_lock:
        try:
            save_progress(set())
        except OSError:
            app.logger.exception("could not save progress")
            return progress_error()
        for n in model_history:
            generation[n] += 1
            model_history[n] = []
            chat_log[n] = []
    return jsonify(ok=True)


if __name__ == "__main__":
    print(f"\n  LLM Injection Lab  ->  http://localhost:5000   (model: {MODEL})\n")
    for warning in config_warnings():
        print(f"  [aviso] {warning}")
    app.run(host="127.0.0.1", port=5000)  # local only: this app is vulnerable on purpose
