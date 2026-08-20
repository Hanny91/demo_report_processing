"""
Stage 0 spike: authenticate to Podio as a user via OAuth, fetch one report
item, follow its relationship field to the linked profile item, and print
the raw shape of every field on both items.

Usage:
    python podio_oauth_spike.py                 # run the full spike
    python podio_oauth_spike.py --list-items     # find a valid item_id first

Docs consulted (developers.podio.com): authentication/server_side,
doc/oauth-authorization/get-access-token-22359, doc/items/get-item-22360.
Those two OAuth pages disagreed with each other on the token-exchange
Content-Type (one said application/json, the other recommended
application/x-www-form-urlencoded). Verified against the live API: it's
JSON — form-urlencoded gets a 400 "Invalid value null (null): must be
object", because Podio parses the body as JSON and finds nothing there.
"""

import argparse
import json
import os
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from dotenv import load_dotenv

TOKEN_CACHE_PATH = Path(__file__).parent / ".podio_token_cache.json"
AUTHORIZE_URL = "https://podio.com/oauth/authorize"
TOKEN_URL = "https://api.podio.com/oauth/token/v2"
API_BASE = "https://api.podio.com"


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required env var {name}. Copy .env.example to .env and fill it in.")
    return value


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        self.server.auth_code = qs.get("code", [None])[0]
        self.server.auth_error = qs.get("error", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body>Authorized. You can close this tab.</body></html>")

    def log_message(self, fmt, *args):
        pass  # silence default request logging


def get_authorization_code(client_id: str, redirect_uri: str) -> str:
    parsed = urlparse(redirect_uri)
    server = HTTPServer((parsed.hostname, parsed.port or 80), _CallbackHandler)
    server.auth_code = None
    server.auth_error = None

    auth_url = f"{AUTHORIZE_URL}?{urlencode({'client_id': client_id, 'redirect_uri': redirect_uri})}"
    print(f"Opening browser for Podio authorization:\n  {auth_url}\n")
    webbrowser.open(auth_url)
    print(f"Waiting for redirect on {redirect_uri} ...")
    server.handle_request()  # blocks for exactly one HTTP request
    server.server_close()

    if server.auth_error:
        raise SystemExit(f"Podio returned an OAuth error: {server.auth_error}")
    if not server.auth_code:
        raise SystemExit("No 'code' parameter received on the callback.")
    return server.auth_code


def exchange_code_for_token(client_id: str, client_secret: str, redirect_uri: str, code: str) -> dict:
    resp = httpx.post(
        TOKEN_URL,
        json={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        },
    )
    if resp.status_code != 200:
        print(f"Token exchange failed ({resp.status_code}). Raw response body:")
        print(resp.text)
        resp.raise_for_status()
    return resp.json()


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    resp = httpx.post(
        TOKEN_URL,
        json={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
    )
    resp.raise_for_status()
    return resp.json()


def load_cached_token() -> dict | None:
    if not TOKEN_CACHE_PATH.exists():
        return None
    return json.loads(TOKEN_CACHE_PATH.read_text())


def save_token_cache(token_data: dict) -> None:
    cache = {**token_data, "expires_at": time.time() + token_data["expires_in"] - 60}
    TOKEN_CACHE_PATH.write_text(json.dumps(cache, indent=2))


def get_access_token(client_id: str, client_secret: str, redirect_uri: str) -> str:
    cache = load_cached_token()
    if cache and cache.get("expires_at", 0) > time.time():
        return cache["access_token"]

    if cache and cache.get("refresh_token"):
        try:
            token_data = refresh_access_token(client_id, client_secret, cache["refresh_token"])
            save_token_cache(token_data)
            return token_data["access_token"]
        except httpx.HTTPStatusError:
            print("Cached refresh token was rejected — falling back to full authorization flow.")

    code = get_authorization_code(client_id, redirect_uri)
    token_data = exchange_code_for_token(client_id, client_secret, redirect_uri, code)
    save_token_cache(token_data)
    return token_data["access_token"]


def get_item(access_token: str, item_id: str) -> dict:
    resp = httpx.get(
        f"{API_BASE}/item/{item_id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    resp.raise_for_status()
    return resp.json()


# Candidate `fields=` query values for shrinking the response, taken from
# Podio's "Concepts and conventions" docs page — unverified against the live
# API until try_narrow_fields() below actually runs one.
NARROW_FIELD_CANDIDATES = [
    "app.view(micro)",
    "app.view(mini)",
    "app.view(micro).fields(space.view(micro))",
]


def try_narrow_fields(access_token: str, item_id: str) -> None:
    """Try each candidate `fields=` value against GET /item/{id} and print
    what actually comes back (or the error), so we know the real syntax
    instead of trusting the docs' summary of it."""
    for fields_value in NARROW_FIELD_CANDIDATES:
        print(f"\n--- fields={fields_value!r} ---")
        resp = httpx.get(
            f"{API_BASE}/item/{item_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": fields_value},
        )
        print(f"status={resp.status_code}  response_bytes={len(resp.content)}")
        if resp.status_code != 200:
            print(resp.text)
            continue
        body = resp.json()
        app_field = find_relationship_field(body)
        if app_field is None:
            print("(no relationship field on this item to inspect)")
            continue
        print("relationship field's raw value with this fields= applied:")
        print(json.dumps(app_field.get("values"), indent=2))


def list_items(access_token: str, app_id: str, limit: int = 20) -> None:
    resp = httpx.post(
        f"{API_BASE}/item/app/{app_id}/filter/",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"limit": limit},
    )
    resp.raise_for_status()
    data = resp.json()
    print(f"Found {data.get('total', '?')} items in app {app_id}:")
    for it in data.get("items", []):
        print(f"  item_id={it['item_id']}  app_item_id={it.get('app_item_id')}  title={it.get('title')}")


def find_relationship_field(item_json: dict) -> dict | None:
    for field in item_json.get("fields", []):
        if field.get("type") == "app":
            return field
    return None


def extract_linked_item_id(app_field: dict) -> int:
    """Pull item_id out of a relationship field's raw values.

    Shape is unverified beyond the spec's note that the read side nests
    item_id/title — this takes the first item_id it can find anywhere in
    the raw values so the script doesn't have to guess the exact nesting.
    """
    for v in app_field.get("values", []):
        value = v.get("value", v)
        if isinstance(value, dict) and "item_id" in value:
            return value["item_id"]
    raise SystemExit(
        "Could not find item_id in the relationship field's raw value.\n"
        f"Raw field JSON:\n{json.dumps(app_field, indent=2)}"
    )


def print_field_summary(label: str, item_json: dict) -> None:
    print(f"\n=== {label}: field summary (external_id, type, raw value) ===")
    for field in item_json.get("fields", []):
        print(f"\n--- external_id={field.get('external_id')!r}  type={field.get('type')!r} ---")
        print(json.dumps(field.get("values"), indent=2))


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--list-items",
        action="store_true",
        help="Authenticate, list items in PODIO_REPORT_APP_ID (item_id/app_item_id/title), then exit.",
    )
    parser.add_argument(
        "--narrow",
        action="store_true",
        help="Try candidate fields= query values against PODIO_REPORT_ITEM_ID to see what actually shrinks the response, then exit.",
    )
    args = parser.parse_args()

    client_id = require_env("PODIO_CLIENT_ID")
    client_secret = require_env("PODIO_CLIENT_SECRET")
    redirect_uri = os.environ.get("PODIO_REDIRECT_URI", "http://localhost:8080/callback")

    access_token = get_access_token(client_id, client_secret, redirect_uri)

    if args.list_items:
        app_id = require_env("PODIO_REPORT_APP_ID")
        list_items(access_token, app_id)
        return

    if args.narrow:
        report_item_id = require_env("PODIO_REPORT_ITEM_ID")
        try_narrow_fields(access_token, report_item_id)
        return

    report_item_id = require_env("PODIO_REPORT_ITEM_ID")

    print(f"\n=== Fetching report item {report_item_id} ===")
    report = get_item(access_token, report_item_id)
    print(json.dumps(report, indent=2))

    app_field = find_relationship_field(report)
    if app_field is None:
        raise SystemExit("No field of type 'app' found on the report item — cannot follow relationship.")
    profile_item_id = extract_linked_item_id(app_field)

    print(f"\n=== Fetching linked profile item {profile_item_id} ===")
    profile = get_item(access_token, profile_item_id)
    print(json.dumps(profile, indent=2))

    print_field_summary("REPORT", report)
    print_field_summary("PROFILE", profile)


if __name__ == "__main__":
    main()
