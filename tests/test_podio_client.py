import io
import json
import os
import time
import urllib.error

import pytest

from yw.podio import client as client_module
from yw.podio.client import PodioClient, PodioError, load_env_file


class FakeResponse:
    def __init__(self, body: dict, headers: dict | None = None):
        self._raw = json.dumps(body).encode()
        self.headers = headers or {}

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakePodio:
    """Stands in for urlopen: records requests, replays queued responses."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def http_error(code: int, body: dict) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("url", code, "err", {}, io.BytesIO(json.dumps(body).encode()))


@pytest.fixture
def cache(tmp_path):
    return tmp_path / "token.json"


def make_client(cache):
    return PodioClient("id", "secret", "http://localtest.me:8080/callback", token_cache=cache)


def install(monkeypatch, fake):
    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake)
    return fake


def no_browser(*args):
    raise AssertionError("browser sign-in should not be needed")


# --- tokens ------------------------------------------------------------------


def test_valid_cached_token_is_used_without_signing_in(monkeypatch, cache):
    cache.write_text(json.dumps({"access_token": "abc", "expires_at": time.time() + 600}))
    monkeypatch.setattr(client_module, "_authorization_code", no_browser)
    fake = install(monkeypatch, FakePodio(FakeResponse({"item_id": 1})))

    assert make_client(cache).get("/item/1") == {"item_id": 1}
    assert fake.requests[0].get_header("Authorization") == "Bearer abc"


def test_expired_token_is_refreshed_and_cached(monkeypatch, cache):
    cache.write_text(json.dumps({"access_token": "old", "refresh_token": "r1", "expires_at": 0}))
    monkeypatch.setattr(client_module, "_authorization_code", no_browser)
    fake = install(monkeypatch, FakePodio(
        FakeResponse({"access_token": "new", "expires_in": 28800}),
        FakeResponse({}),
    ))

    make_client(cache).get("/item/1")

    token_request = json.loads(fake.requests[0].data)
    assert token_request["grant_type"] == "refresh_token"
    assert token_request["refresh_token"] == "r1"
    assert fake.requests[1].get_header("Authorization") == "Bearer new"
    saved = json.loads(cache.read_text())
    assert saved["access_token"] == "new"
    assert saved["refresh_token"] == "r1"  # kept when Podio doesn't send a new one


def test_rejected_refresh_token_falls_back_to_sign_in(monkeypatch, cache):
    cache.write_text(json.dumps({"access_token": "old", "refresh_token": "bad", "expires_at": 0}))
    monkeypatch.setattr(client_module, "_authorization_code", lambda *a: "code123")
    fake = install(monkeypatch, FakePodio(
        http_error(400, {"error": "invalid_grant"}),
        FakeResponse({"access_token": "fresh", "refresh_token": "r2", "expires_in": 28800}),
        FakeResponse({}),
    ))

    make_client(cache).get("/item/1")

    assert json.loads(fake.requests[1].data)["code"] == "code123"
    assert fake.requests[2].get_header("Authorization") == "Bearer fresh"


def test_token_is_fetched_once_per_run(monkeypatch, cache):
    cache.write_text(json.dumps({"access_token": "old", "refresh_token": "r1", "expires_at": 0}))
    fake = install(monkeypatch, FakePodio(
        FakeResponse({"access_token": "new", "expires_in": 28800}),
        FakeResponse({}),
        FakeResponse({}),
    ))

    client = make_client(cache)
    client.get("/item/1")
    client.get("/item/2")

    assert len(fake.requests) == 3


# --- requests ----------------------------------------------------------------


@pytest.fixture
def signed_in(monkeypatch, cache):
    cache.write_text(json.dumps({"access_token": "abc", "expires_at": time.time() + 600}))
    return make_client(cache)


def test_post_sends_json(monkeypatch, signed_in):
    fake = install(monkeypatch, FakePodio(FakeResponse({"item_id": 7})))

    assert signed_in.post("/item/app/5/", {"fields": {"title": "x"}}) == {"item_id": 7}
    request = fake.requests[0]
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data) == {"fields": {"title": "x"}}


def test_error_message_hides_response_body(monkeypatch, signed_in):
    body = {"error": "invalid_value", "error_description": 'Invalid value "threw the compass"'}
    install(monkeypatch, FakePodio(http_error(400, body)))

    with pytest.raises(PodioError) as info:
        signed_in.post("/item/app/5/", {})

    assert str(info.value) == "POST /item/app/5/ failed: 400 invalid_value"
    assert "compass" not in str(info.value)
    assert info.value.status == 400
    assert b"compass" in info.value.body


def test_rate_limit_remaining_is_recorded(monkeypatch, signed_in):
    install(monkeypatch, FakePodio(FakeResponse({}, headers={"X-Rate-Limit-Remaining": "987"})))
    signed_in.get("/item/1")
    assert signed_in.rate_limit_remaining == 987


# --- .env ----------------------------------------------------------------------


def test_load_env_file(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text('# comment\n\nYW_TEST_A=one\nYW_TEST_B="two"\nYW_TEST_C=from-file\n')
    monkeypatch.setenv("YW_TEST_C", "from-env")
    for key in ("YW_TEST_A", "YW_TEST_B"):
        monkeypatch.delenv(key, raising=False)

    load_env_file(env)

    assert os.environ["YW_TEST_A"] == "one"
    assert os.environ["YW_TEST_B"] == "two"
    assert os.environ["YW_TEST_C"] == "from-env"
    for key in ("YW_TEST_A", "YW_TEST_B"):
        monkeypatch.delenv(key)
