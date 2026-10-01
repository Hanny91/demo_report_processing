import io
import json
import urllib.error

import pytest

from yw.extract import llm
from yw.extract.llm import LLMError, check_ready


def fake_urlopen(body=None, error=None):
    def urlopen(url, timeout):
        if error:
            raise error
        return io.BytesIO(json.dumps(body).encode())
    return urlopen


def test_check_ready_passes_when_model_is_installed(monkeypatch):
    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen({"data": [{"id": llm.MODEL_NAME}]}))
    check_ready()


def test_check_ready_accepts_untagged_name_listed_as_latest(monkeypatch):
    monkeypatch.setattr(llm, "MODEL_NAME", "llama3.1-8b-cpu")
    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen({"data": [{"id": "llama3.1-8b-cpu:latest"}]}))
    check_ready()


def test_check_ready_names_the_missing_model(monkeypatch):
    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen({"data": [{"id": "other:1b"}]}))
    with pytest.raises(LLMError, match="ollama pull"):
        check_ready()


def test_check_ready_says_when_ollama_is_down(monkeypatch):
    error = urllib.error.URLError(ConnectionRefusedError("refused"))
    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen(error=error))
    with pytest.raises(LLMError, match="isn't reachable"):
        check_ready()
