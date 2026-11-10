"""LLM Injection Lab - game server.

Run with:  python app.py   and open http://127.0.0.1:5000

The browser never receives prompts or passwords: everything secret stays
here, and the page only gets the bot answers and the portal dashboards.
"""
import ipaddress
import json
import math
import mimetypes
import os
import pathlib
import re
import threading
import time
from urllib.parse import urlsplit

from flask import Flask, jsonify, request, send_from_directory

# Flask picks Content-Type from Python's mimetypes, which on Windows reads the
# registry, where some installers leave .js as text/plain. With nosniff the
# browser would then refuse to run the game's script. Say it explicitly.
def fix_mimetypes() -> None:
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/css", ".css")


fix_mimetypes()

from levels import LEVELS
from levels.base import Level, wrap_file
from llm import MAX_TOKENS, MODEL, NUM_CTX, OLLAMA_URL, LLMError, chat, int_env, warm

ROOT = pathlib.Path(__file__).parent
PROGRESS_FILE = pathlib.Path(os.environ.get("LAB_PROGRESS", ROOT / "progress.json"))
PROGRESS_VERSION = 1  # bump when the file format changes, so old files are recognized
# 5000 is taken by AirPlay on recent Macs: LAB_PORT=5050 python app.py
PORT = int_env("LAB_PORT", 5000)
if PORT > 65535:
    raise SystemExit(f"LAB_PORT must be a port between 1 and 65535 (got {PORT}).")
# 127.0.0.1, not localhost: localhost may try IPv6 (::1) first, where another
# program can sit on the same port.
GAME_URL = f"http://127.0.0.1:{PORT}"
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
# A line like the ones in docs/sample-log.txt, to size a realistic full file.
TYPICAL_LOG_LINE = "[2026-11-05 08:01] server web-01: CPU 97% for 10 minutes\n"
# Names this server answers to. Anything else is a DNS rebinding attempt: an
# evil site pointing its own domain at 127.0.0.1 to talk to your game.
LOCAL_HOSTS = {"localhost", "127.0.0.1"}
# Only this server's own files may run or style the page (no inline scripts,
# no inline styles), fetch() only reaches this server, and no other site can
# frame the game. The bot answers are always shown as text (textContent);
# if that ever breaks, injected markup still can't run a script.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'self'")
# Origins of the game's own page, as browsers send them in the Origin header.
GAME_ORIGINS = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}

app = Flask(__name__, static_folder="static")
by_number = {lv.number: lv for lv in LEVELS}
model_history = {lv.number: [] for lv in LEVELS}  # what the model sees
chat_log = {lv.number: [] for lv in LEVELS}       # what the player sees
# Bumped by reset/restart: a reply that started before it must not write back.
generation = {lv.number: 0 for lv in LEVELS}
model_lock = threading.Lock()  # one answer at a time, the CPU is small
# What the model read last (level, generation, turns): Ollama has it cached,
# so warming it again would only burn CPU. Guarded by state_lock.
in_cache = {"key": None}
state_lock = threading.Lock()  # guards the dicts above and the progress file


# ---------- helpers ----------
def in_background(fn) -> None:
    threading.Thread(target=fn, daemon=True).start()


def count(value) -> int:
    return value if type(value) is int and value >= 0 else 0


def moment(value) -> float | None:
    """A real past timestamp, or None (Infinity, NaN and future times are not)."""
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return float(value) if 0 < value <= time.time() + 60 else None


def clean_stats(raw) -> dict:
    """Stats for the final report, rebuilt from scratch so a hand-edited file can't break it.

    started/finished: time of the first counted message and of the admin login.
    levels: {"1": {"sent": messages until the level was won, "blocked": how many a filter caught}}
    """
    raw = raw if isinstance(raw, dict) else {}
    levels = raw.get("levels") if isinstance(raw.get("levels"), dict) else {}
    clean = {}
    for lv in LEVELS:
        one = levels.get(str(lv.number))
        if isinstance(one, dict):
            sent = count(one.get("sent"))
            clean[str(lv.number)] = {"sent": sent, "blocked": min(count(one.get("blocked")), sent)}
    return {"started": moment(raw.get("started")), "finished": moment(raw.get("finished")), "levels": clean}


def retry_locked(fn):
    """On Windows, the antivirus, the search indexer or OneDrive can hold the
    progress file for a few milliseconds: wait a little instead of failing."""
    for attempt in range(5):
        try:
            return fn()
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05)


def sibling(suffix: str) -> pathlib.Path:
    """progress.json -> progress.json.tmp: never the progress file itself,
    whatever name LAB_PROGRESS gives it."""
    return PROGRESS_FILE.with_name(PROGRESS_FILE.name + suffix)


class ProgressUnreadable(OSError):
    """The progress file is there but can't be read or moved aside: stop,
    never treat it as "no progress" (the next save would overwrite it)."""


def set_aside(why: str) -> None:
    """Keep an unreadable progress file (progress.json.bad-<time>) instead of
    silently overwriting it with the next save."""
    stamp = time.strftime(".bad-%Y%m%d-%H%M%S")
    bad, n = sibling(stamp), 1
    while bad.exists():  # two bad files in the same second: keep both
        n += 1
        bad = sibling(f"{stamp}-{n}")
    try:
        retry_locked(lambda: os.replace(PROGRESS_FILE, bad))
    except OSError as e:
        raise ProgressUnreadable(f"{PROGRESS_FILE} {why} and could not be moved aside") from e
    app.logger.warning("%s %s: moved it to %s and started from zero.", PROGRESS_FILE, why, bad)


def read_raw() -> dict:
    """The progress file as a dict: {} when there is none yet.

    Errors other than "no file" (no permission, a folder in its place...) are
    raised, never read as "no progress": the next save would wipe the real one.
    """
    try:
        data = retry_locked(PROGRESS_FILE.read_bytes)
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise ProgressUnreadable(f"could not read {PROGRESS_FILE}") from e
    try:
        # utf-8-sig: PowerShell 5 and Notepad may add a BOM when editing by hand.
        raw = json.loads(data.decode("utf-8-sig"))
    except (ValueError, RecursionError):  # bad bytes, bad JSON, absurdly deep JSON
        set_aside("is not valid JSON")
        return {}
    if not isinstance(raw, dict):
        set_aside("is not a JSON object")
        return {}
    version = raw.get("version", 1)  # files from before the version field are 1
    if type(version) is not int or version > PROGRESS_VERSION:
        set_aside("comes from a newer version of the game")
        return {}
    return raw


def read_progress() -> tuple[set[int], dict]:
    raw = read_raw()
    found = raw.get("cleared")
    found = {n for n in found if type(n) is int} if isinstance(found, list) else set()
    cleared, n = set(), 1
    while n in found and n in by_number:  # levels are won in order: stop at the first gap
        cleared.add(n)
        n += 1
    stats = clean_stats(raw.get("stats"))
    if LEVELS[-1].number not in cleared:  # can't have finished without the last level
        stats["finished"] = None
    return cleared, stats


def load_progress() -> set[int]:
    return read_progress()[0]


def cost(text: str) -> int:
    """Worst-case size of a text in cost units, where 2 units = 1 token."""
    def one(ch: str) -> int:
        if ch == " " or (ch.isascii() and ch.isalpha()):
            return 1
        return CHARS_PER_TOKEN * (1 if ch.isascii() else len(ch.encode("utf-8")))

    return sum(one(ch) for ch in text)


def message_cost(content: str) -> int:
    return cost(content) + MESSAGE_OVERHEAD


def save_progress(cleared: set[int], stats: dict | None = None) -> None:
    """Write to a temp file first, so a crash mid-write never wipes the progress.

    Without stats, the ones already saved are kept.
    """
    if stats is None:
        stats = read_progress()[1]
    data = json.dumps({"version": PROGRESS_VERSION, "cleared": sorted(cleared), "stats": stats},
                      allow_nan=False)
    tmp = sibling(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())  # on disk before the swap, even if the power goes out
        retry_locked(lambda: os.replace(tmp, PROGRESS_FILE))
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass  # never hide the real error behind a cleanup one
        raise


def record_attempt(number: int, blocked: bool) -> None:
    """Count a message for the final report (call under state_lock).

    Only messages until the level is won count. Stats are a bonus: if they
    can't be read or saved, the chat goes on.
    """
    try:
        cleared, stats = read_progress()
        if number in cleared:
            return
        stats["started"] = stats["started"] or time.time()
        one = stats["levels"].setdefault(str(number), {"sent": 0, "blocked": 0})
        one["sent"] += 1
        one["blocked"] += int(blocked)
        save_progress(cleared, stats)
    except OSError:
        app.logger.exception("could not save stats")


def build_report(stats: dict) -> dict:
    """The "invasion report" shown when the admin logs in."""
    levels = []
    for lv in LEVELS:
        one = stats["levels"].get(str(lv.number), {"sent": 0, "blocked": 0})
        levels.append({"number": lv.number, "name": lv.bot_name, "avatar": lv.avatar, **one})
    sent = sum(x["sent"] for x in levels)
    blocked = sum(x["blocked"] for x in levels)
    seconds = None
    if stats["started"] and stats["finished"] and stats["finished"] >= stats["started"]:
        seconds = round(stats["finished"] - stats["started"])

    badges = [{"icon": "👑", "name": "Owner of Acme", "text": "Reached admin just by talking."}]
    # Level 1 has no defense, so winning it at the first try proves nothing.
    first_try = [x["number"] for x in levels[1:] if x["sent"] == 1]
    if first_try:
        badges.append({"icon": "🎯", "name": "First shot",
                       "text": "Won at the first try: level" + "s" * (len(first_try) > 1) + " "
                               + ", ".join(map(str, first_try)) + "."})
    if sent and not blocked:
        badges.append({"icon": "👻", "name": "Ghost", "text": "No filter caught you in the whole game."})
    if seconds is not None and seconds < 30 * 60:
        badges.append({"icon": "⚡", "name": "Lightning", "text": "Broke in everywhere in under 30 minutes."})
    if sent >= 40:
        badges.append({"icon": "🧱", "name": "Stubborn", "text": "Kept pushing until the wall fell."})
    return {"seconds": seconds, "sent": sent, "blocked": blocked, "levels": levels, "badges": badges}


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
    settings = f"LAB_CTX={NUM_CTX} with LAB_MAX_TOKENS={MAX_TOKENS}"
    needed = typical_turn_cost()
    warnings = []
    ollama_host = ollama_host_or_none()
    if ollama_host is None:
        warnings.append(f"OLLAMA_URL looks wrong ({OLLAMA_URL}): it should look like "
                        "http://127.0.0.1:11434.")
    elif not is_loopback(ollama_host):
        warnings.append(f"OLLAMA_URL points to another machine ({ollama_host}): prompts and "
                        "messages cross the network unencrypted, and Ollama has no password. "
                        "Prefer an SSH tunnel or a VPN and keep OLLAMA_URL on 127.0.0.1.")
    for lv in LEVELS:
        budget = context_budget(lv)
        if budget <= MESSAGE_OVERHEAD:
            warnings.append(f"Level {lv.number}: {settings} leaves no room for any "
                            "message; all of them will be refused.")
        elif budget < needed:
            warnings.append(f"Level {lv.number}: {settings} is tight; long messages or logs "
                            "near the limit will be refused as too dense.")
    return warnings


def ollama_host_or_none() -> str | None:
    """Host of OLLAMA_URL, or None when the URL is malformed (no http://,
    a bad port, a broken IPv6 address...)."""
    try:
        parts = urlsplit(OLLAMA_URL)
        parts.port  # raises on a bad port
    except ValueError:
        return None
    return parts.hostname if parts.scheme in ("http", "https") else None


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


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


def valid_text(text: str) -> str:
    """Replace lone surrogates ("\\ud800" is valid JSON but not valid UTF-8) with "?",
    so they can never break cost(), the log or the request to Ollama."""
    return text.encode("utf-8", "replace").decode("utf-8")


def text_of(value) -> str:
    """Only real strings count: null, numbers or lists become empty text."""
    return valid_text(value) if isinstance(value, str) else ""


def entry(sender: str, text: str, pending: bool = False) -> dict:
    return {"from": sender, "text": text, "time": time.strftime("%H:%M"), "pending": pending}


def add_to_log(number: int, *entries: dict) -> None:
    """Append to a contact's log, keeping only the newest entries (call under state_lock)."""
    chat_log[number] = (chat_log[number] + list(entries))[-MAX_LOG_ENTRIES:]


def progress_error():
    return error("Could not save your progress: check the LAB_PROGRESS folder.", 500)


def claim_progress_file():
    """Lock the progress file for this process, for as long as it runs.

    Two copies of the game on the same file would overwrite each other's
    saves. The OS drops the lock when the process ends, even after a crash,
    so a stale lock never blocks the next start. Keep the returned file open.
    """
    path = sibling(".lock")
    try:
        f = open(path, "a+")
    except OSError as e:
        raise SystemExit(f"Can't create {path}: {e.strerror}. Check the LAB_PROGRESS folder.")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise SystemExit(f"Another copy of the game is already running with {PROGRESS_FILE}. "
                         "Close it first, or give this one its own LAB_PROGRESS file.")
    return f


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
        return error(f"Open the game at {GAME_URL}.", 403)
    if request.method == "POST":
        if not request.is_json:
            return error("Send JSON.", 415)
        # Extra layer: browsers say which page sent the request. Only this game's own.
        # The Host was already checked as local, so its own origin is fine too
        # (that covers `flask run --port N` without LAB_PORT).
        origin = request.headers.get("Origin")
        if origin is not None and origin not in GAME_ORIGINS | {request.host_url.rstrip("/")}:
            return error("Requests from other sites are not allowed.", 403)


@app.after_request
def security_headers(response):
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.errorhandler(ProgressUnreadable)
def progress_unreadable(e):
    """The progress file exists but can't be read (permissions, a folder in its place...)."""
    app.logger.exception("could not read progress")
    return error("Could not read your progress: check the LAB_PROGRESS file.", 500)


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
        return error("This contact is locked.", 403)

    message = text_of(data.get("message")).strip()
    if len(message) > MAX_MESSAGE_CHARS:
        return error(f"Message too long (max. {MAX_MESSAGE_CHARS} characters).", 400)
    file_name, file_text = None, None
    attached = data.get("file")
    if lv.accepts_files and isinstance(attached, dict):
        file_name = clean_file_name(text_of(attached.get("name")))
        file_text = text_of(attached.get("text"))
        if len(file_text) > MAX_FILE_CHARS:
            return error(f"File too large (max. {MAX_FILE_CHARS} characters).", 400)
    if not message and file_text is None:
        return error("Empty message.", 400)

    content = message or "Please summarize the attached file."
    if file_text is not None:
        content += "\n\n" + wrap_file(file_name, file_text)
    if message_cost(content) > context_budget(lv):
        # Numbers, symbols, emoji or other scripts can cost a token per
        # character: past this point the turn would push the defenses out of
        # the model's context.
        return error("Text too dense (lots of digits, symbols, emoji or accents): "
                     "send less text.", 400)

    if file_text is not None and not file_text.strip():
        # Nothing to read: don't spend a slow model call summarizing nothing.
        return error("Empty file: write something in the .txt before attaching it.", 400)

    shown = f"📎 {file_name}" + (f"\n{message}" if message else "") if file_name else message

    refused = lv.files_only and (file_text is None or message)
    if refused:
        # The only way in is the file itself: typed text would turn this
        # level back into a direct injection (level 2) and skip its lesson.
        blocked = "📎 This contact only reads .txt files: attach the file without typing any text."
    else:
        # Weakness on purpose: the input filter only sees the typed message, never the file.
        blocked = lv.check_input(message) if lv.check_input and message else None

    with state_lock:  # same lock for the checks and the log, so a restart can't slip in between
        if not is_unlocked(lv.number, load_progress()):
            return error("This contact is locked.", 403)
        if any(e["pending"] for e in chat_log[lv.number]):  # e.g. a second tab
            return error("Wait for the bot to answer your last message.", 409)
        if blocked:
            add_to_log(lv.number, entry("me", shown), entry("sys", blocked))
            if not refused:  # a wrong kind of message is not an attempt
                record_attempt(lv.number, blocked=True)
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
                    return error("The chat was cleared before the answer arrived.", 409)
                history = trim_history(
                    model_history[lv.number] + [{"role": "user", "content": content}],
                    context_budget(lv),
                )
            raw = valid_text(chat(lv.system_prompt, history, lv.examples))
            if not raw.strip():  # small models sometimes answer nothing at all
                raise LLMError("The bot answered nothing. Try again or change your message.")
            blocked = lv.check_output(raw) if lv.check_output else None
            answer = blocked or raw
            with state_lock:
                if gen != generation[lv.number]:
                    return error("The chat was cleared before the answer arrived.", 409)
                model_history[lv.number] = history + [{"role": "assistant", "content": answer}]
                in_cache["key"] = (lv.number, gen, len(history) + 1)  # same as model_history now
                add_to_log(lv.number, entry("sys" if blocked else "bot", answer))
                record_attempt(lv.number, blocked=bool(blocked))
        return jsonify(answer=answer, blocked=bool(blocked))
    except Exception as e:  # LLMError has a friendly text; anything else gets a generic one
        if isinstance(e, LLMError):
            text = str(e)
        elif isinstance(e, ProgressUnreadable):
            text = "Could not read your progress: check the LAB_PROGRESS file."
        else:
            text = "Unexpected error while talking to the model."
        with state_lock:
            if gen == generation[lv.number]:
                add_to_log(lv.number, entry("sys", f"⚠️ {text}"))
        if not isinstance(e, LLMError):
            app.logger.exception("unexpected error in /api/chat")
        return error(text, 503 if isinstance(e, LLMError) else 500)
    finally:
        with state_lock:
            mine["pending"] = False  # never leave the page "typing" forever


@app.post("/api/warm")
def warm_up():
    """Read the bot's prompt ahead of time, while the player types (see llm.warm)."""
    lv = level_from(body())
    if lv is None:
        return jsonify(ok=False)

    def run():
        if not model_lock.acquire(blocking=False):
            return  # a real answer is on its way: never make it wait
        try:
            warm_if_needed()
        except ProgressUnreadable:
            app.logger.exception("warm-up skipped")
        finally:
            model_lock.release()

    def warm_if_needed():  # call with model_lock held
        with state_lock:
            if not is_unlocked(lv.number, load_progress()):
                return
            if any(e["pending"] for e in chat_log[lv.number]):
                return
            key = (lv.number, generation[lv.number], len(model_history[lv.number]))
            if in_cache["key"] == key:
                return
            history = trim_history(list(model_history[lv.number]), context_budget(lv))
        if warm(lv.system_prompt, history, lv.examples):
            with state_lock:
                in_cache["key"] = key

    in_background(run)
    return jsonify(ok=True)


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
            final = lv.number == LEVELS[-1].number
            with state_lock:
                cleared, stats = read_progress()
                if not is_unlocked(lv.number, cleared):
                    return error("Valid login, but you haven't reached this team yet. "
                                 "Follow the order of the contacts.", 403)
                if final:
                    stats["finished"] = stats["finished"] or time.time()
                try:
                    save_progress(cleared | {lv.number}, stats)
                except OSError:
                    app.logger.exception("could not save progress")
                    return progress_error()
            return jsonify(
                ok=True,
                level=lv.number,
                username=lv.username,
                dashboard=lv.dashboard,
                debrief=lv.debrief,
                final=final,
                report=build_report(stats) if final else None,
            )
    return error("Invalid username or password.", 401)


@app.post("/api/restart")
def restart_game():
    with state_lock:
        read_raw()  # a broken file is moved aside first, never wiped
        try:
            save_progress(set(), clean_stats(None))
        except OSError:
            app.logger.exception("could not save progress")
            return progress_error()
        for n in model_history:
            generation[n] += 1
            model_history[n] = []
            chat_log[n] = []
    return jsonify(ok=True)


if __name__ == "__main__":
    lock = claim_progress_file()  # held until the server stops
    print(f"\n  LLM Injection Lab  ->  {GAME_URL}   (model: {MODEL})\n")
    for warning in config_warnings():
        print(f"  [warning] {warning}")
    app.run(host="127.0.0.1", port=PORT)  # local only: this app is vulnerable on purpose
