"""
One-time provisioning script: creates the 'Suggested Profile Updates' app in
a Podio space via POST /app/, with every field defined in code — including
external_ids — instead of clicking it together by hand in the UI. See
podio-review-app-decision.md for the app's design and podio.md's schema
table in yw-reports-demo-spec.md §5 for the field list this mirrors.


Podio's app-creation endpoint isn't verified against live behavior yet the
way the OAuth/item endpoints in podio_oauth_spike.py and seed_reports.py
were. This script prints the created app's raw JSON — including the
external_id Podio actually assigned each field — after creation, so that can
be confirmed before the extraction script is written against it. Same
"verify against the live response, don't trust the docs" approach as the
rest of stage 0.

Does NOT set up: the default value on the `status` category field, app
sharing/permissions, or the filtered "Needs review" view. Those need
account-specific choices (who gets access, what the view is called) that
don't belong hardcoded in a provisioning script — the script prints a
checklist of them at the end instead.

Usage:
    python setup_podio_app.py                # create the app + fields
    python setup_podio_app.py --dry-run       # print the payload, don't call the API

Requires in .env (in addition to the OAuth vars in podio_oauth_spike.py):
    PODIO_SPACE_ID        — space to create the app in
    PODIO_PROFILE_APP_ID  — existing Child Profile app; referenced by `child`
    PODIO_REPORT_APP_ID   — existing Daily Report app; referenced by `source-reports`
"""

import argparse
import json
import os

import httpx
from dotenv import load_dotenv

from podio_oauth_spike import API_BASE, get_access_token, require_env

TARGET_FIELD_OPTIONS = ["presentation", "boundaries", "triggers", "projects-and-activities"]
STATUS_OPTIONS = ["proposed", "accepted", "edited", "rejected"]


def build_app_payload(profile_app_id: str, report_app_id: str) -> dict:
    return {
        "config": {
            "name": "Suggested Profile Updates",
            "item_name": "Proposal",
            "description": (
                "Model-proposed updates to Child Profile fields, extracted from "
                "Daily Report items, for a human to accept, edit, or reject. "
                "Additive only — never written to by anything other than this "
                "app's own extraction script."
            ),
            "external_id": "suggested_profile_updates",
            "icon": "3.png",
        },
        "fields": [
            {
                "type": "app",
                "config": {
                    "label": "Child",
                    "external_id": "child",
                    "settings": {
                        "referenced_apps": [{"app_id": int(profile_app_id)}],
                        "multiple": False,
                    },
                },
            },
            {
                "type": "category",
                "config": {
                    "label": "Target field",
                    "external_id": "target-field",
                    "settings": {
                        "options": [{"text": o} for o in TARGET_FIELD_OPTIONS],
                        "multiple": False,
                        "display": "list",
                    },
                },
            },
            {
                "type": "text",
                "config": {
                    "label": "Proposed value",
                    "external_id": "proposed-value",
                    "settings": {"size": "large"},
                },
            },
            {
                "type": "app",
                "config": {
                    "label": "Source reports",
                    "external_id": "source-reports",
                    "settings": {
                        "referenced_apps": [{"app_id": int(report_app_id)}],
                        "multiple": True,
                    },
                },
            },
            {
                "type": "number",
                "config": {
                    "label": "Evidence count",
                    "external_id": "evidence-count",
                    "settings": {"decimals": 0},
                },
            },
            {
                "type": "category",
                "config": {
                    "label": "Status",
                    "external_id": "status",
                    "settings": {
                        "options": [{"text": o} for o in STATUS_OPTIONS],
                        "multiple": False,
                        "display": "list",
                    },
                },
            },
            {
                "type": "contact",
                "config": {
                    "label": "Reviewed by",
                    "external_id": "reviewed-by",
                    "settings": {"multiple": False},
                },
            },
            {
                "type": "date",
                "config": {
                    "label": "Reviewed at",
                    "external_id": "reviewed-at",
                    "settings": {"calendar": False},
                },
            },
            {
                "type": "text",
                "config": {
                    "label": "Model version",
                    "external_id": "model-version",
                    "settings": {"size": "small"},
                },
            },
        ],
    }


def create_app(access_token: str, space_id: str, payload: dict) -> dict:
    resp = httpx.post(
        f"{API_BASE}/app/",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"space_id": int(space_id), **payload},
    )
    if resp.status_code not in (200, 201):
        print(f"App creation failed ({resp.status_code}). Raw response body:")
        print(resp.text)
        resp.raise_for_status()
    return resp.json()


def get_app(access_token: str, app_id: int) -> dict:
    resp = httpx.get(
        f"{API_BASE}/app/{app_id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    resp.raise_for_status()
    return resp.json()


def print_field_summary(app_json: dict) -> None:
    print("\n=== Field summary (label -> external_id, type) ===")
    for field in app_json.get("fields", []):
        config = field.get("config", {})
        print(f"  {config.get('label')!r:30s} -> external_id={field.get('external_id')!r:20s} type={field.get('type')!r}")


def print_followup_checklist(app_id: int) -> None:
    print(
        f"""
=== Manual follow-up (not scripted — needs account-specific decisions) ===

1. Set the default value on the 'Status' field to 'proposed', so new items
   land in the review queue without the script having to set it explicitly
   every time. App -> Settings -> Fields -> Status -> edit -> default value.

2. Grant access: Space -> Settings -> Sharing (or this app's own Rights
   panel, if the space restricts per-app). Coordinators need write access
   for the extraction script's own account; instructors need at least
   read/write on this app to review and change 'status' -- they don't need
   any new access to the Daily Report or Child Profile apps beyond what
   they already have.

3. Create the review queue: App -> Views -> + Create view. Filter
   status = proposed, group by child, sort by evidence-count descending.
   Name it something like "Needs review" and set it as the app's default
   view -- this is the entire review UI, nothing else to build.

4. Sanity-check the field shapes before pointing the extraction script at
   this app: create one test item by hand (App id: {app_id}), fetch it via
   GET /item/{{item_id}}, and confirm the category/relationship field value
   shapes match what the script expects to write -- same "don't guess"
   check as stage 0's item field-shape verification.
"""
    )


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the app-creation payload and exit without calling the API.",
    )
    args = parser.parse_args()

    profile_app_id = require_env("PODIO_PROFILE_APP_ID")
    report_app_id = require_env("PODIO_REPORT_APP_ID")
    payload = build_app_payload(profile_app_id, report_app_id)

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return

    client_id = require_env("PODIO_CLIENT_ID")
    client_secret = require_env("PODIO_CLIENT_SECRET")
    redirect_uri = os.environ.get("PODIO_REDIRECT_URI", "http://localhost:8080/callback")
    space_id = require_env("PODIO_SPACE_ID")

    access_token = get_access_token(client_id, client_secret, redirect_uri)

    print(f"Creating 'Suggested Profile Updates' app in space {space_id} ...")
    result = create_app(access_token, space_id, payload)
    app_id = result["app_id"]
    print(f"Created app_id={app_id}")

    print("\nFetching the app back to confirm the field shapes Podio actually assigned...")
    app_json = get_app(access_token, app_id)
    print_field_summary(app_json)
    print_followup_checklist(app_id)


if __name__ == "__main__":
    main()
