"""llm.py checks with a fake HTTP layer: no Ollama needed."""
import pytest
import requests

import llm


class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self):
        if self._payload is None:  # what requests >= 2.27 really raises
            raise requests.JSONDecodeError("not json", "<html>", 0)
        return self._payload


def test_sends_prompt_examples_history_and_limits(monkeypatch):
    sent = {}

    def fake_post(url, json, timeout):
        sent.update(json)
        return FakeResponse(payload={"message": {"content": "hi"}})

    monkeypatch.setattr(llm.requests, "post", fake_post)
    ex = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]
    assert llm.chat("SYS", [{"role": "user", "content": "now"}], ex) == "hi"
    assert [m["content"] for m in sent["messages"]] == ["SYS", "q", "a", "now"]
    opts = sent["options"]
    assert opts["num_ctx"] == llm.NUM_CTX and opts["num_predict"] == llm.MAX_TOKENS
    assert opts["num_thread"] == llm.NUM_THREADS and sent["stream"] is False


@pytest.mark.parametrize("exc, words", [
    (requests.ConnectionError(), "não está rodando"),
    (requests.Timeout(), "demorou"),
    (requests.exceptions.InvalidSchema(), "Não deu pra falar"),
])
def test_network_errors_become_friendly_messages(monkeypatch, exc, words):
    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(llm.requests, "post", boom)
    with pytest.raises(llm.LLMError, match=words):
        llm.chat("SYS", [])


def test_ollama_error_shows_only_its_message(monkeypatch):
    payload = {"error": 'model "gemma3:1b" not found'}
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: FakeResponse(404, payload))
    with pytest.raises(llm.LLMError) as e:
        llm.chat("SYS", [])
    assert 'model "gemma3:1b" not found' in str(e.value) and "{" not in str(e.value)


@pytest.mark.parametrize("payload", [None, {}, {"message": None}, ["x"], {"message": {"content": None}}])
def test_weird_replies_become_friendly_messages(monkeypatch, payload):
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: FakeResponse(200, payload))
    with pytest.raises(llm.LLMError, match="inesperada"):
        llm.chat("SYS", [])


def test_int_env_reads_numbers_and_explains_bad_values(monkeypatch):
    monkeypatch.setenv("LAB_TEST_NUMBER", "8")
    assert llm.int_env("LAB_TEST_NUMBER", 2) == 8
    monkeypatch.delenv("LAB_TEST_NUMBER")
    assert llm.int_env("LAB_TEST_NUMBER", 2) == 2
    monkeypatch.setenv("LAB_TEST_NUMBER", "4k")
    with pytest.raises(SystemExit, match="LAB_TEST_NUMBER precisa ser um número inteiro"):
        llm.int_env("LAB_TEST_NUMBER", 2)
    for bad in ["0", "-3"]:  # LAB_TIMEOUT=0 would look like a bad Ollama answer
        monkeypatch.setenv("LAB_TEST_NUMBER", bad)
        with pytest.raises(SystemExit, match="maior que zero"):
            llm.int_env("LAB_TEST_NUMBER", 2)


@pytest.mark.parametrize("base", ["http://localhost:11434", "http://localhost:11434/", "http://pc:11434//"])
def test_chat_url_never_doubles_the_slash(base):
    assert llm.chat_url(base).endswith(":11434/api/chat")


def test_warm_sends_the_same_prefix_as_chat_and_one_token(monkeypatch):
    sent = []
    monkeypatch.setattr(llm.requests, "post", lambda url, json, timeout: sent.append(json) or FakeResponse(payload={"message": {"content": "x"}}))
    ex = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]
    llm.warm("SYS", [], ex)
    llm.chat("SYS", [{"role": "user", "content": "now"}], ex)
    assert sent[1]["messages"][: len(sent[0]["messages"])] == sent[0]["messages"]
    assert sent[0]["options"]["num_predict"] == 1
    assert sent[0]["options"]["num_ctx"] == sent[1]["options"]["num_ctx"]


def test_warm_never_raises(monkeypatch):
    def down(url, json, timeout):
        raise requests.ConnectionError()
    monkeypatch.setattr(llm.requests, "post", down)
    llm.warm("SYS", [])
