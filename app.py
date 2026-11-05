"""LLM Injection Lab - game server.

Run with:  python app.py   and open http://localhost:5000

The browser never receives prompts or passwords: everything secret stays
here, and the page only gets the bot answers and the portal dashboards.
"""
import json
import os
import pathlib
import threading

from flask import Flask, jsonify, request, send_from_directory

from levels import LEVELS
from llm import MODEL, LLMError, chat

ROOT = pathlib.Path(__file__).parent
PROGRESS_FILE = pathlib.Path(os.environ.get("LAB_PROGRESS", ROOT / "progress.json"))
TOTAL_LEVELS = 6
COMING_SOON = [  # shown locked in the messenger until they exist
    {"number": 5, "name": "Suporte de TI", "avatar": "🛠️"},
    {"number": 6, "name": "Assistente do CEO", "avatar": "👑"},
]

app = Flask(__name__, static_folder="static")
by_number = {lv.number: lv for lv in LEVELS}
model_history = {lv.number: [] for lv in LEVELS}  # what the model sees
chat_log = {lv.number: [] for lv in LEVELS}       # what the player sees
model_lock = threading.Lock()  # one answer at a time, the CPU is small


def load_progress() -> set[int]:
    try:
        return set(json.loads(PROGRESS_FILE.read_text())["cleared"])
    except (FileNotFoundError, ValueError, KeyError):
        return set()


def save_progress(cleared: set[int]) -> None:
    PROGRESS_FILE.write_text(json.dumps({"cleared": sorted(cleared)}))


def is_unlocked(number: int, cleared: set[int]) -> bool:
    return number == 1 or (number - 1) in cleared


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/state")
def state():
    cleared = load_progress()
    contacts = [
        {
            "number": lv.number,
            "name": lv.bot_name,
            "avatar": lv.avatar,
            "greeting": lv.greeting,
            "unlocked": is_unlocked(lv.number, cleared),
            "cleared": lv.number in cleared,
            "log": chat_log[lv.number],
        }
        for lv in LEVELS
    ]
    contacts += [{**c, "unlocked": False, "soon": True} for c in COMING_SOON]
    return jsonify(contacts=contacts, cleared=sorted(cleared), total=TOTAL_LEVELS, model=MODEL)


@app.post("/api/chat")
def send_message():
    data = request.get_json(silent=True) or {}
    lv = by_number.get(data.get("level"))
    if lv is None or not is_unlocked(lv.number, load_progress()):
        return jsonify(error="Contato bloqueado."), 403
    message = str(data.get("message", "")).strip()[:1000]
    if not message:
        return jsonify(error="Mensagem vazia."), 400

    blocked = lv.check_input(message) if lv.check_input else None
    if blocked:
        answer = blocked
    else:
        history = model_history[lv.number] + [{"role": "user", "content": message}]
        try:
            with model_lock:
                raw = chat(lv.system_prompt, history, lv.examples)
        except LLMError as e:
            return jsonify(error=str(e)), 503
        blocked = lv.check_output(raw) if lv.check_output else None
        answer = blocked or raw
        model_history[lv.number] = history + [{"role": "assistant", "content": answer}]

    chat_log[lv.number] += [
        {"from": "me", "text": message},
        {"from": "sys" if blocked else "bot", "text": answer},
    ]
    return jsonify(answer=answer, blocked=bool(blocked))


@app.post("/api/reset")
def reset_chat():
    number = (request.get_json(silent=True) or {}).get("level")
    if number in by_number:
        model_history[number] = []
        chat_log[number] = []
    return jsonify(ok=True)


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip().lower()
    password = str(data.get("password", "")).strip()
    for lv in LEVELS:
        if username == lv.username and password == lv.secret:
            cleared = load_progress() | {lv.number}
            save_progress(cleared)
            return jsonify(
                ok=True,
                level=lv.number,
                username=lv.username,
                dashboard=lv.dashboard,
                final=lv.number == TOTAL_LEVELS,
            )
    return jsonify(ok=False, error="Usuário ou senha inválidos."), 401


@app.post("/api/restart")
def restart_game():
    save_progress(set())
    for n in model_history:
        model_history[n] = []
        chat_log[n] = []
    return jsonify(ok=True)


if __name__ == "__main__":
    print(f"\n  LLM Injection Lab  ->  http://localhost:5000   (model: {MODEL})\n")
    app.run(port=5000)
