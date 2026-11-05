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

from flask import Flask, jsonify, request, send_from_directory

from levels import LEVELS
from levels.base import Level, wrap_file
from llm import MODEL, LLMError, chat

ROOT = pathlib.Path(__file__).parent
PROGRESS_FILE = pathlib.Path(os.environ.get("LAB_PROGRESS", ROOT / "progress.json"))
MAX_MESSAGE_CHARS = 1000
MAX_FILE_CHARS = 4000       # attached .txt files (small model, small context)
HISTORY_CHAR_BUDGET = 8000  # older turns are dropped so prompt + chat fit the context

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


def save_progress(cleared: set[int]) -> None:
    """Write to a temp file first, so a crash mid-write never wipes the progress."""
    tmp = PROGRESS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"cleared": sorted(cleared)}))
    os.replace(tmp, PROGRESS_FILE)


def is_unlocked(number: int, cleared: set[int]) -> bool:
    return number == 1 or (number - 1) in cleared


def trim_history(history: list[dict], budget: int = HISTORY_CHAR_BUDGET) -> list[dict]:
    """Drop the oldest user/bot pairs until the history fits the budget.

    The newest message is always kept. Without this, a long chat pushes the
    system prompt out of the model's context and the defenses disappear.
    """
    history = list(history)
    while len(history) > 1 and sum(len(m["content"]) for m in history) > budget:
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
def only_json_posts():
    """A cross-site <form> can't send application/json without a CORS preflight,
    which this server never answers, so other pages can't reset your game."""
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
            chat_log[lv.number] += [entry("me", shown), entry("sys", blocked)]
            return jsonify(answer=blocked, blocked=True)
        # Log the question right away, so a page reload still shows it (and
        # the "typing" bubble) while the model works.
        gen = generation[lv.number]
        mine = entry("me", shown, pending=True)
        chat_log[lv.number].append(mine)

    content = message or "Please summarize the attached file."
    if file_text is not None:
        content += "\n\n" + wrap_file(file_name, file_text)

    try:
        with model_lock:  # one model call at a time; read, call and write together
            with state_lock:
                if gen != generation[lv.number] or not is_unlocked(lv.number, load_progress()):
                    return error("A conversa foi apagada antes da resposta.", 409)
                history = trim_history(
                    model_history[lv.number] + [{"role": "user", "content": content}]
                )
            raw = chat(lv.system_prompt, history, lv.examples)
            blocked = lv.check_output(raw) if lv.check_output else None
            answer = blocked or raw
            with state_lock:
                if gen != generation[lv.number]:
                    return error("A conversa foi apagada antes da resposta.", 409)
                model_history[lv.number] = history + [{"role": "assistant", "content": answer}]
                chat_log[lv.number].append(entry("sys" if blocked else "bot", answer))
        return jsonify(answer=answer, blocked=bool(blocked))
    except Exception as e:  # LLMError has a friendly text; anything else gets a generic one
        text = str(e) if isinstance(e, LLMError) else "Erro inesperado ao falar com o modelo."
        with state_lock:
            if gen == generation[lv.number]:
                chat_log[lv.number].append(entry("sys", f"⚠️ {text}"))
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
                save_progress(cleared | {lv.number})
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
        save_progress(set())
        for n in model_history:
            generation[n] += 1
            model_history[n] = []
            chat_log[n] = []
    return jsonify(ok=True)


if __name__ == "__main__":
    print(f"\n  LLM Injection Lab  ->  http://localhost:5000   (model: {MODEL})\n")
    app.run(host="127.0.0.1", port=5000)  # local only: this app is vulnerable on purpose
