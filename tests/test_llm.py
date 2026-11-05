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
        if self._payload is None:
            raise ValueError("not json")
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


@pytest.mark.parametrize("payload", [None, {}, {"message": None}, ["x"]])
def test_weird_replies_become_friendly_messages(monkeypatch, payload):
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: FakeResponse(200, payload))
    with pytest.raises(llm.LLMError, match="inesperada"):
        llm.chat("SYS", [])
