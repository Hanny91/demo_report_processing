"""
Podio API access (spec §7, §13): OAuth as the coordinator running the batch,
and one request wrapper that read.py and write.py go through.

Uses only the standard library, like llm.py. The auth flow is the one stage 0
tested against the live API (podio_api_work/podio_oauth_spike.py):
authorization-code sign-in in the browser, then refresh tokens. The token
exchange takes a JSON body, not a form-encoded one (project.md appendix,
finding 1).

The token cache (.podio_token_cache.json, gitignored) is the only thing this
module writes to disk. It holds credentials, never report or profile content,
so the no-persistence rule (spec §5) doesn't apply to it. The spike used the
same file.

Error messages carry the method, path, HTTP status and Podio's short error
code. They never include response bodies, which can echo field values back
(spec §5: never log field values or report text). The body is kept on
PodioError.body for debugging, and str() never shows it.

MVP scope: no rate limiting and no retries. A run for one child makes a
handful of calls, far below 1,000 per hour (spec §7). The remaining allowance
Podio reports is kept in `rate_limit_remaining`, so run.py can print it.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

API_BASE = "https://api.podio.com"
AUTHORIZE_URL = "https://podio.com/oauth/authorize"
TOKEN_URL = f"{API_BASE}/oauth/token/v2"

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = REPO_ROOT / ".env"
TOKEN_CACHE_PATH = REPO_ROOT / ".podio_token_cache.json"

TIMEOUT = 30.0

# Treat a token as expired this many seconds early, so it can't expire
# between the check and the request.
EXPIRY_MARGIN = 60


class PodioError(RuntimeError):
    """
    A Podio call failed. str() shows the method, path, status and Podio's
    error code only. `body` holds the raw response for debugging and is
    never part of the message.
    """

    def __init__(self, message: str, status: int | None = None, body: bytes | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


class PodioClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        token_cache: Path = TOKEN_CACHE_PATH,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._token_cache = token_cache
        self._token: dict | None = None
        self.rate_limit_remaining: int | None = None

    @classmethod
    def from_env(cls, env_path: Path = ENV_PATH) -> "PodioClient":
        """Build a client from PODIO_CLIENT_ID, PODIO_CLIENT_SECRET and PODIO_REDIRECT_URI."""
        load_env_file(env_path)
        return cls(
            _require_env("PODIO_CLIENT_ID"),
            _require_env("PODIO_CLIENT_SECRET"),
            _require_env("PODIO_REDIRECT_URI"),
        )

    def get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params=params)

    def post(self, path: str, body: dict) -> dict:
        return self._request("POST", path, body=body)

    def _request(
        self, method: str, path: str, body: dict | None = None, params: dict | None = None
    ) -> dict:
        url = f"{API_BASE}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        return _send(method, url, path, body, headers, on_headers=self._note_rate_limit)

    def _note_rate_limit(self, headers) -> None:
        remaining = headers.get("X-Rate-Limit-Remaining")
        if remaining is not None and remaining.isdigit():
            self.rate_limit_remaining = int(remaining)

    # --- auth ---------------------------------------------------------------

    def _access_token(self) -> str:
        if self._token is None:
            self._token = _load_token_cache(self._token_cache)

        if self._token and self._token.get("expires_at", 0) > time.time():
            return self._token["access_token"]

        if self._token and self._token.get("refresh_token"):
            try:
                return self._store_token(
                    self._token_request(
                        {"grant_type": "refresh_token", "refresh_token": self._token["refresh_token"]}
                    )
                )
            except PodioError as exc:
                if exc.status is None:
                    raise  # network failure, not a rejected token
                print("Cached refresh token was rejected; signing in again.")

        code = _authorization_code(self._client_id, self._redirect_uri)
        return self._store_token(
            self._token_request(
                {"grant_type": "authorization_code", "redirect_uri": self._redirect_uri, "code": code}
            )
        )

    def _token_request(self, grant: dict) -> dict:
        body = {"client_id": self._client_id, "client_secret": self._client_secret, **grant}
        return _send("POST", TOKEN_URL, "/oauth/token/v2", body, headers={})

    def _store_token(self, token: dict) -> str:
        if "refresh_token" not in token and self._token:
            token["refresh_token"] = self._token.get("refresh_token")
        token["expires_at"] = time.time() + token["expires_in"] - EXPIRY_MARGIN
        self._token = token
        self._token_cache.write_text(json.dumps(token, indent=2))
        return token["access_token"]


def load_env_file(path: Path) -> None:
    """
    Read KEY=VALUE lines from a .env file into os.environ. Variables already
    set in the environment win. Blank lines and # comments are skipped. A
    missing file is fine: the variables may come from the environment.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required env var {name}. Copy .env.example to .env and fill it in.")
    return value


def _send(method, url, path, body, headers, on_headers=None) -> dict:
    """One HTTP call. `path` names the call in error messages, without the host or query."""
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers = {**headers, "Content-Type": "application/json"}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            if on_headers:
                on_headers(response.headers)
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        raise PodioError(
            f"{method} {path} failed: {exc.code} {_error_code(raw)}", status=exc.code, body=raw
        ) from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise PodioError(f"{method} {path} failed: {exc}") from exc

    return json.loads(raw) if raw else {}


def _error_code(raw: bytes) -> str:
    """Podio's short error code (e.g. 'not_found'), never its description."""
    try:
        code = json.loads(raw).get("error")
    except (ValueError, AttributeError):
        return ""
    return code if isinstance(code, str) else ""


def _load_token_cache(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except ValueError:
        return None


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self.server.auth_code = query.get("code", [None])[0]
        self.server.auth_error = query.get("error", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body>Authorized. You can close this tab.</body></html>")

    def log_message(self, fmt, *args):
        pass  # the query string holds the auth code


def _authorization_code(client_id: str, redirect_uri: str) -> str:
    """
    Browser sign-in: open Podio's authorize page and catch the redirect on
    a one-shot local server. The redirect host can be localtest.me, which
    resolves to 127.0.0.1 (project.md appendix, finding 2).
    """
    parsed = urllib.parse.urlparse(redirect_uri)
    server = HTTPServer((parsed.hostname, parsed.port or 80), _CallbackHandler)
    server.auth_code = None
    server.auth_error = None

    query = urllib.parse.urlencode({"client_id": client_id, "redirect_uri": redirect_uri})
    auth_url = f"{AUTHORIZE_URL}?{query}"
    print(f"Opening browser for Podio sign-in:\n  {auth_url}\nWaiting for redirect on {redirect_uri} ...")
    webbrowser.open(auth_url)
    server.handle_request()  # blocks for exactly one request
    server.server_close()

    if server.auth_error:
        raise SystemExit(f"Podio returned an OAuth error: {server.auth_error}")
    if not server.auth_code:
        raise SystemExit("No 'code' parameter received on the callback.")
    return server.auth_code
