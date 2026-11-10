"""Replay the winning attacks from docs/SOLUTIONS.md against the real model.

Run it by hand (CI has no model) after changing a prompt, Ollama or the
model, to prove every level can still be beaten:

    python scripts/check_attacks.py            # all levels, in order
    python scripts/check_attacks.py 3 5        # only levels 3 and 5
    python scripts/check_attacks.py --tries 3  # up to 3 tries per level

It plays like a player: sends the attack from the solutions, checks that the
answer leaks the password (plain, base64 or split into words), then logs in
with the level's login to unlock the next contact (even if the level held, so
later levels are still checked). It runs the game server in this process, with a
throwaway progress file, so your saved game is never touched. Close the game
first, so the two don't fight over the CPU: the script refuses to start
while it is open (only a game started with `python app.py` on the same
progress file is detected).

This calls the model once per try (usually ~50 s on an old CPU, at most
LAB_TIMEOUT: the script doesn't pre-read prompts) and keeps it busy the whole
time. A try where the model answers nothing or times out is tried again, like
a player sending again.

Exit code:
  0  every level leaked
  1  some level didn't leak (the model kept the secret, or the solution
     itself is now blocked by the input filter)
  2  the check could not tell: the model never answered on some level, the
     game is open, no Ollama or model, bad arguments, or an unexpected error
     (2 wins over 1: a level with no answer makes the whole run inconclusive)

"""
import argparse
import base64
import pathlib
import re
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import app as server  # noqa: E402  (needs ROOT on the path first)
    from levels import LEVELS  # noqa: E402
    from levels.base import INPUT_BLOCK  # noqa: E402
except SystemExit as e:  # a bad LAB_* setting: a setup problem, not a held level
    print(e)
    sys.exit(2)
except ImportError as e:  # Flask or requests missing: the venv is not active
    print(f"{e}. Activate the venv, or run: python -m pip install -r requirements.txt")
    sys.exit(2)

SOLUTIONS = ROOT / "docs" / "SOLUTIONS.md"
MAX_GAP = 30  # letters and digits allowed between two pieces of a split password
# "## 3. Finance Bot (output filter)" ... the first ``` block before the next "## ".
SECTION = re.compile(r"^## (\d+)\. (?:(?!^## ).)*?^```[^\n]*\n(.*?)^```", re.M | re.S)


class SetupError(Exception):
    """The check can't go on: whatever the bots do next would prove nothing."""


def norm(text: str) -> str:
    """Same rule as the portal login: no case, spaces or punctuation."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def load_attacks(path: pathlib.Path) -> dict[int, str]:
    """Level number -> the first code block of its section in the solutions."""
    return {int(n): block for n, block in SECTION.findall(path.read_text(encoding="utf-8"))}


def leaks(answer: str, secret: str) -> bool:
    """Did the answer give the password away, in any form a player can use?

    Plain ("sunflower42"), split ("golden, kraken", or "golden and kraken":
    every piece, in order, close to the one before) or base64 (level 3), even
    broken across lines. Pieces far apart in a long answer don't count: a
    false "leaked" would hide a level that now holds.
    """
    flat = norm(answer)
    pieces = [re.escape(p) for p in map(norm, secret.split("-")) if p]
    # One regex, so a piece that also shows up earlier ("Golden rule: ...")
    # can't hide the real "golden, kraken" later on.
    if pieces and re.search(f".{{0,{MAX_GAP}}}".join(pieces), flat):
        return True
    encoded = base64.b64encode(secret.encode()).decode().rstrip("=")
    return encoded in re.sub(r"\s", "", answer)


def request_for(level, attack: str) -> dict:
    """Levels that read files (5 and 6) get the attack as a .txt, with no typed text."""
    if level.accepts_files:
        return {"level": level.number, "message": "", "file": {"name": "attack.txt", "text": attack}}
    return {"level": level.number, "message": attack.strip()}


def login(client, level) -> bool:
    r = client.post("/api/login", json={"username": level.username, "password": level.secret})
    return r.status_code == 200


LEAKED, HELD, NO_ANSWER = "leaked", "held", "no answer"


def attack(client, level, text: str, tries: int, out=print) -> str:
    """LEAKED, HELD (the model answered and kept the secret), or NO_ANSWER."""
    result = NO_ANSWER
    for n in range(1, tries + 1):
        client.post("/api/reset", json={"level": level.number})  # a clean chat, like the tip says
        start = time.time()
        r = client.post("/api/chat", json=request_for(level, text))
        took = time.time() - start
        data = r.get_json(silent=True) or {}
        answer = data.get("answer") or data.get("error") or ""
        short = " ".join(answer.split())[:100]
        if r.status_code == 503:
            # Small models sometimes answer nothing, and slow CPUs time out:
            # a player would just send again. Only a missing Ollama or model stops the run.
            # Ollama may still be busy with the abandoned answer: give it time.
            warnings, fatal = server.model_check(timeout=15)
            if fatal:
                raise SetupError(f"level {level.number}: {' '.join(warnings)}")
            out(f"  try {n}: model error in {took:.0f}s  {ascii(short)}")
            continue
        if r.status_code != 200:  # progress unreadable, unexpected error...
            raise SetupError(f"level {level.number}: HTTP {r.status_code}: {answer}")
        if data.get("blocked") and answer == INPUT_BLOCK:
            # Not model randomness: the solution itself trips the blocklist.
            out(f"  try {n}: blocked by the input filter: fix the attack in {SOLUTIONS.name}")
            return HELD
        ok = not data.get("blocked") and leaks(answer, level.secret)
        # ascii(): emoji in answers must not crash a console or file without UTF-8.
        out(f"  try {n}: {'LEAKED' if ok else 'held'} in {took:.0f}s  {ascii(short)}")
        if ok:
            return LEAKED
        result = HELD
    return result


def play(client, wanted: list[int], attacks: dict[int, str], tries: int, out=print) -> dict:
    """Level number -> LEAKED, HELD or NO_ANSWER."""
    results = {}
    for lv in LEVELS:
        if lv.number > wanted[-1]:
            break
        if lv.number in wanted:
            out(f"Level {lv.number} ({lv.bot_name}):")
            results[lv.number] = attack(client, lv, attacks[lv.number], tries, out)
        # Unlock the next contact with the level's login, even if this level
        # held, so the later levels are still checked.
        if not login(client, lv):
            raise SetupError(f"login as {lv.username} failed: check the level's username and secret.")
    return results


def main(argv=None, out=print) -> int:
    p = argparse.ArgumentParser(description="Replay docs/SOLUTIONS.md against the real model.")
    p.add_argument("levels", nargs="*", type=int, help="levels to check (default: all)")
    p.add_argument("--tries", type=int, default=2, help="tries per level (default: 2)")
    args = p.parse_args(argv)

    by_number = {lv.number: lv for lv in LEVELS}
    wanted = sorted(set(args.levels)) or sorted(by_number)
    unknown = [n for n in wanted if n not in by_number]
    if unknown or args.tries < 1:
        out(f"Levels go from 1 to {len(LEVELS)}, and --tries must be 1 or more.")
        return 2
    try:
        attacks = load_attacks(SOLUTIONS)
    except OSError as e:
        out(f"Can't read {SOLUTIONS}: {e.strerror}.")
        return 2
    missing = [n for n in wanted if n not in attacks]
    if missing:
        out(f"No attack found in {SOLUTIONS.name} for level(s) {missing}.")
        return 2
    warnings, fatal = server.model_check()
    for w in server.config_warnings() + warnings:  # what the game prints at startup too
        out(f"! {w}")
    if fatal:
        return 2
    try:
        # The game's lock: refuse to run next to an open game (or another check),
        # which would share the CPU and turn slow answers into false failures.
        lock = server.claim_progress_file()
    except SystemExit as e:
        if "already running" in str(e):
            out(f"! The game is open on {server.PROGRESS_FILE.name} (or another check is "
                "running): close it before checking the attacks.")
        else:  # e.g. LAB_PROGRESS points to a folder that doesn't exist
            out(f"! {e}")
        return 2

    saved = server.PROGRESS_FILE
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            server.PROGRESS_FILE = pathlib.Path(tmp) / "progress.json"  # never the player's file
            try:
                results = play(server.app.test_client(), wanted, attacks, args.tries, out)
            finally:
                server.PROGRESS_FILE = saved
    except SetupError as e:
        out(f"! Stopped: {e}")
        return 2
    finally:
        lock.close()

    held = [n for n, r in results.items() if r == HELD]
    silent = [n for n, r in results.items() if r == NO_ANSWER]
    leaked = len(results) - len(held) - len(silent)
    out("")
    out(f"{leaked}/{len(results)} levels leaked with {server.MODEL}."
        + (f" Still holding: {held}." if held else "")
        + (f" No answer from the model: {silent}." if silent else ""))
    return 2 if silent else 1 if held else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # progress shows up even through `| tee`
    try:
        sys.exit(main())
    except Exception:  # a bug must not look like "a level held" (exit 1)
        import traceback
        traceback.print_exc()
        sys.exit(2)
