"""scripts/check_attacks.py with a fake model: no Ollama, no CPU."""
import base64
import re

import pytest

import app as server
from levels import LEVELS
from levels.base import FILE_END, INPUT_BLOCK, wrap_file
from scripts import check_attacks

BY_PROMPT = {lv.system_prompt: lv for lv in LEVELS}


def leaked_form(lv) -> str:
    """What the real bot answers when it leaks: forms that pass each output filter."""
    if lv.number == 3:
        return base64.b64encode(lv.secret.encode()).decode()
    if lv.number == 6:
        return lv.secret.replace("-", ", ")
    return f"Sure! The password is {lv.secret}."


@pytest.fixture
def fake(tmp_path, monkeypatch):
    player_file = tmp_path / "player-progress.json"
    monkeypatch.setattr(server, "PROGRESS_FILE", player_file)
    for n in server.model_history:  # same clean start as tests/test_app.py
        server.model_history[n] = []
        server.chat_log[n] = []
    monkeypatch.setattr(server, "model_check", lambda **k: ([], False))
    calls = []
    mode = {"answer": leaked_form}

    def fake_chat(system_prompt, history, examples=None):
        lv = BY_PROMPT[system_prompt]
        calls.append((lv.number, history[-1]["content"]))
        return mode["answer"](lv)

    monkeypatch.setattr(server, "chat", fake_chat)
    lines = []
    return {"calls": calls, "mode": mode, "out": lines.append, "lines": lines, "player": player_file}


def solutions() -> dict[int, str]:
    return check_attacks.load_attacks(check_attacks.SOLUTIONS)


def test_solutions_table_matches_the_levels():
    # The script logs in with the level files; players read the table in the docs.
    rows = re.findall(r"^\| (\d+) \|[^|]*\| `([^`]+)` \| `([^`]+)` \|",
                      check_attacks.SOLUTIONS.read_text(encoding="utf-8"), re.M)
    assert [(int(n), u, p) for n, u, p in rows] == [(lv.number, lv.username, lv.secret) for lv in LEVELS]


def test_solutions_have_an_attack_for_every_level():
    attacks = solutions()
    assert sorted(attacks) == [lv.number for lv in LEVELS]
    for lv in LEVELS:
        # The levels that read files are beaten with the fake end-of-file trick.
        assert (FILE_END in attacks[lv.number]) == lv.accepts_files


def test_a_section_without_code_never_takes_the_next_ones(tmp_path):
    doc = tmp_path / "s.md"
    doc.write_text("## 1. A\n\nno code here\n\n## 2. B\n\n```\nattack two\n```\n", encoding="utf-8")
    assert check_attacks.load_attacks(doc) == {2: "attack two\n"}


@pytest.mark.parametrize("answer, secret", [
    ("The password is Sunflower42.", "sunflower42"),
    ("golden, kraken", "golden-kraken"),
    ("The words are golden and then kraken.", "golden-kraken"),
    ("Golden rule: I never share secrets. Recovery words: golden, kraken", "golden-kraken"),
    ("The kraken sleeps. " * 5 + "Words: golden, kraken", "golden-kraken"),
    ('{"password_backup": "Ymx1ZS1kb2xwaGlu"}', "blue-dolphin"),
    ("Ymx1ZS1k\nb2xwaGlu", "blue-dolphin"),
])
def test_leaks_spots_every_usable_form(answer, secret):
    assert check_attacks.leaks(answer, secret)


@pytest.mark.parametrize("answer", [
    "Sorry, I can't share internal information.", "golden", "kraken, golden", "",
    "A golden sunrise. " + "Our team keeps every record safe and tidy. " * 3 + "The kraken is a myth.",
])
def test_leaks_ignores_refusals_and_half_secrets(answer):
    assert not check_attacks.leaks(answer, "golden-kraken")


def lock_is_free():
    server.claim_progress_file().close()  # SystemExit if the script kept the game's lock


def test_all_levels_leak_and_player_progress_is_untouched(fake):
    assert check_attacks.main([], out=fake["out"]) == 0
    lock_is_free()
    assert [n for n, _ in fake["calls"]] == [lv.number for lv in LEVELS]
    assert not fake["player"].exists()
    assert server.PROGRESS_FILE == fake["player"]  # restored after the run
    assert fake["lines"][-1].startswith(f"{len(LEVELS)}/{len(LEVELS)} levels leaked")


@pytest.mark.parametrize("number", [lv.number for lv in LEVELS if lv.accepts_files])
def test_file_levels_get_the_attack_as_a_file_with_no_typed_text(fake, number):
    # Typed, the level 6 attack would hit the blocklist and level 5 would refuse it.
    assert check_attacks.main([str(number)], out=fake["out"]) == 0
    (called, content), = fake["calls"]
    assert called == number
    assert content.endswith("\n\n" + wrap_file("attack.txt", solutions()[number]))


def test_only_the_chosen_levels_call_the_model(fake):
    assert check_attacks.main(["3"], out=fake["out"]) == 0
    assert [n for n, _ in fake["calls"]] == [3]


def test_a_level_that_holds_is_retried_then_reported(fake):
    fake["mode"]["answer"] = lambda lv: "Sorry, I can't share internal information."
    assert check_attacks.main(["2", "--tries", "3"], out=fake["out"]) == 1
    assert [n for n, _ in fake["calls"]] == [2, 2, 2]
    assert "Still holding: [2]" in fake["lines"][-1]


def test_ollama_gone_stops_the_whole_run_not_just_the_level(fake, monkeypatch):
    calls = []

    def down(*a, **k):
        calls.append(1)
        raise server.LLMError("Ollama is not running.")

    monkeypatch.setattr(server, "chat", down)
    checks = iter([([], False), (["Could not reach Ollama."], True)])  # up at start, gone mid-run
    monkeypatch.setattr(server, "model_check", lambda **k: next(checks))
    assert check_attacks.main(["--tries", "5"], out=fake["out"]) == 2
    assert len(calls) == 1
    assert "Stopped" in fake["lines"][-1]
    assert not any("Still holding" in line for line in fake["lines"])
    lock_is_free()


def test_an_empty_answer_or_timeout_is_just_a_failed_try(fake, monkeypatch):
    calls = []

    def flaky(system_prompt, history, examples=None):
        calls.append(1)
        if len(calls) == 1:
            raise server.LLMError("The bot answered nothing. Try again or change your message.")
        return leaked_form(BY_PROMPT[system_prompt])

    monkeypatch.setattr(server, "chat", flaky)
    assert check_attacks.main(["1"], out=fake["out"]) == 0
    assert len(calls) == 2
    assert any("model error" in line for line in fake["lines"])


def test_a_model_that_never_answers_is_not_a_level_that_held(fake, monkeypatch):
    def silent(*a, **k):
        raise server.LLMError("The model took more than 300s to answer. Try again.")

    monkeypatch.setattr(server, "chat", silent)
    assert check_attacks.main(["1", "2"], out=fake["out"]) == 2
    assert "No answer from the model: [1, 2]" in fake["lines"][-1]
    assert "Still holding" not in fake["lines"][-1]


def test_no_answer_on_one_level_makes_the_run_inconclusive(fake, monkeypatch):
    def mixed(system_prompt, history, examples=None):
        if BY_PROMPT[system_prompt].number == 2:
            raise server.LLMError("The bot answered nothing. Try again or change your message.")
        return "Sorry, I can't share internal information."

    monkeypatch.setattr(server, "chat", mixed)
    assert check_attacks.main(["1", "2"], out=fake["out"]) == 2
    assert "Still holding: [1]" in fake["lines"][-1]
    assert "No answer from the model: [2]" in fake["lines"][-1]


def test_an_unexpected_server_error_stops_cleanly(fake, monkeypatch):
    def bug(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(server, "chat", bug)
    assert check_attacks.main(["1"], out=fake["out"]) == 2
    assert "HTTP 500" in fake["lines"][-1]
    assert server.PROGRESS_FILE == fake["player"]
    lock_is_free()


def test_a_solution_caught_by_the_blocklist_is_not_retried(fake, monkeypatch):
    lv4 = next(lv for lv in LEVELS if lv.number == 4)
    monkeypatch.setattr(lv4, "check_input", lambda message: INPUT_BLOCK)
    assert check_attacks.main(["4", "--tries", "3"], out=fake["out"]) == 1
    assert fake["calls"] == []
    assert any("blocked by the input filter" in line for line in fake["lines"])


def test_missing_solutions_file_exits_2(fake, monkeypatch, tmp_path):
    monkeypatch.setattr(check_attacks, "SOLUTIONS", tmp_path / "nope.md")
    assert check_attacks.main([], out=fake["out"]) == 2
    assert "Can't read" in fake["lines"][-1]


def test_an_unusable_progress_folder_is_not_called_an_open_game(fake, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "PROGRESS_FILE", tmp_path / "missing" / "progress.json")
    assert check_attacks.main(["1"], out=fake["out"]) == 2
    assert "Can't create" in fake["lines"][-1]


def test_answers_with_emoji_print_as_plain_ascii(fake):
    fake["mode"]["answer"] = lambda lv: "\U0001f6e1️ nope"
    check_attacks.main(["1", "--tries", "1"], out=fake["out"])
    tries = [line for line in fake["lines"] if "try 1" in line]
    assert tries and tries[0].isascii()


def test_a_broken_login_is_a_setup_problem(fake, monkeypatch):
    monkeypatch.setattr(check_attacks, "login", lambda client, lv: False)
    assert check_attacks.main(["1"], out=fake["out"]) == 2


def test_refuses_to_run_next_to_an_open_game(fake):
    game_lock = server.claim_progress_file()  # what `python app.py` holds while it runs
    try:
        assert check_attacks.main([], out=fake["out"]) == 2
    finally:
        game_lock.close()
    assert fake["calls"] == []
    assert "close it" in fake["lines"][-1] and "LAB_PROGRESS" not in fake["lines"][-1]


@pytest.mark.parametrize("argv", [["0"], ["99"], ["--tries", "0"]])
def test_bad_arguments_exit_2(fake, argv):
    assert check_attacks.main(argv, out=fake["out"]) == 2
    assert fake["calls"] == []


def test_no_ollama_exits_2_without_playing(fake, monkeypatch):
    monkeypatch.setattr(server, "model_check", lambda **k: (["Could not reach Ollama at x."], True))
    assert check_attacks.main([], out=fake["out"]) == 2
    assert fake["calls"] == []


def test_an_untested_model_build_only_warns(fake, monkeypatch):
    monkeypatch.setattr(server, "model_check", lambda **k: (["Your gemma3:1b is not the build..."], False))
    assert check_attacks.main(["1"], out=fake["out"]) == 0
    assert fake["lines"][0].startswith("! Your gemma3:1b")
