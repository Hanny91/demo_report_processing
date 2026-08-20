"""
Seed script: create synthetic report items in the sandbox 'reports' app,
all linked to one profile item, so stage 1+ has something to work with
without hand-creating rows in the Podio UI. Mock data only.

Write shapes for the 'date' and 'app' (relationship) fields aren't clearly
documented (see podio_oauth_spike.py's module docstring for the same issue
on the token endpoint). So this creates ONE test item first, prints Podio's
exact response, and asks for confirmation before creating the rest — same
"verify against the live response, don't guess" approach as stage 0.

Usage:
    python seed_reports.py
"""

import json
import os
from datetime import date

import httpx
from dotenv import load_dotenv

from podio_oauth_spike import API_BASE, get_access_token, require_env

REPORTS = [
    {
        "date": date(2026, 6, 12),
        "text": (
            "First proper session outdoors, one to one for the full four hours. Wary "
            "and withdrawn from the start — didn't want to talk about picking an "
            "activity, so I laid out a few options and let him wander over to the "
            "den-building materials on his own. Worked mostly alone for the first "
            "couple of hours, kept his back to me a lot of the time, and gave short "
            "answers whenever I tried to check in. Around the two-hour mark a branch "
            "he was using as a main support snapped; he kicked the rest of the pile "
            "over and walked off toward the treeline for a few minutes without saying "
            "anything. Came back on his own, didn't want to discuss it, and carried on "
            "rebuilding for the rest of the session like nothing had happened."
        ),
    },
    {
        "date": date(2026, 6, 19),
        "text": (
            "Chose fire lighting with flint and steel today, said he'd seen someone do "
            "it once and wanted to try. Watched me demonstrate a couple of times "
            "without saying much, then wanted to have a go himself repeatedly — must "
            "have tried thirty or forty times over the session. Minimal talking "
            "throughout, but he did laugh, briefly, the first time he got a spark to "
            "catch in the tinder, which is the first time I've heard him laugh here. "
            "Two young people from another group walked past mid-session and asked "
            "what he was doing; he gave them a short, factual answer but didn't "
            "engage further or invite them to stay and watch."
        ),
    },
    {
        "date": date(2026, 6, 26),
        "text": (
            "Tree climbing today, and noticeably more vocal than previous weeks — he "
            "was the one who suggested it, and talked me through which tree looked "
            "climbable and why before we'd even started. More confident on the ropes "
            "than I expected given it was his first time, pushing himself a bit higher "
            "each attempt. Grazed his knee fairly badly climbing down near the end of "
            "the session, enough that it was bleeding a little, but he brushed it off, "
            "said it was fine without being asked, and wanted to keep going rather than "
            "stop for a plaster."
        ),
    },
    {
        "date": date(2026, 7, 3),
        "text": (
            "Minibeast hunting near the pond, which took up nearly two hours of the "
            "session — turning logs, checking under bark, and working through the "
            "identification chart together to name what we found. Talked more than "
            "I've heard him talk in any session so far, including asking me several "
            "questions unprompted about how long different bugs live and whether they "
            "bite. General sense over the last few weeks that he's getting more "
            "comfortable starting conversation himself rather than only responding "
            "when spoken to."
        ),
    },
    {
        "date": date(2026, 7, 10),
        "text": (
            "First time using a knife this session, for whittling. Visibly nervous "
            "beforehand — asked several questions about the safety rules before he'd "
            "pick it up, more than he usually asks about anything — but stuck with it "
            "after we went through the safety briefing together. Took most of the "
            "session but he produced a rough spoon shape by the end, and was clearly "
            "proud of it: showed it to me from a few angles and asked if he could take "
            "it home rather than leave it at the base like usual."
        ),
    },
    {
        "date": date(2026, 7, 17),
        "text": (
            "Rope swing over the stream today. Pushed himself to go a little higher "
            "and further out with each attempt, and started narrating it himself — "
            "cheering himself on, counting the swings out loud. Partway through, "
            "another young person from a different session came over to watch, and "
            "Damian offered, unprompted, to show them how the swing worked and where "
            "to hold on. First time I've seen him approach another child himself "
            "rather than waiting to be approached, and he seemed pleased with himself "
            "about it afterward."
        ),
    },
    {
        "date": date(2026, 7, 24),
        "text": (
            "Campfire cooking today — bread twists over a fire he built almost "
            "entirely independently now, only checking with me once on how big to "
            "build it. Partway through cooking he mentioned, quite matter-of-factly, "
            "that he'd had a difficult morning at home before the session. When I "
            "asked gently if he wanted to say more he said no and changed the subject, "
            "so I left it there and didn't push. Otherwise settled and cheerful for "
            "the rest of the session, ate two helpings of the bread twists."
        ),
    },
    {
        "date": date(2026, 7, 31),
        "text": (
            "Chose den building again this week — went straight back to the one from "
            "week one and spent the session repairing and improving it rather than "
            "starting fresh. Much more confident giving me instructions when he wanted "
            "help with a heavier branch, compared to working silently on his own back "
            "in June. Clearly proud of the finished structure, walked me round it "
            "explaining each part. No incidents or low moments today, steady and "
            "settled throughout."
        ),
    },
    {
        "date": date(2026, 8, 7),
        "text": (
            "Orienteering with a map and compass around the site today, a new "
            "activity for him. Worked out two of the three checkpoints unaided, "
            "including correcting himself once when he realised he'd misread the "
            "bearing. Got visibly frustrated at the third checkpoint after several "
            "failed attempts, threw the compass down onto the grass and said he "
            "wanted to stop. Gave him a couple of minutes rather than stepping in "
            "immediately; he calmed down on his own and came back to finish the round "
            "with some support from me on that last leg."
        ),
    },
    {
        "date": date(2026, 8, 14),
        "text": (
            "Free choice session today — he mixed fire lighting, some more tree "
            "climbing, and a bit of repair work on the den. Chatted throughout in a "
            "way that would have been unusual for him back in June, including "
            "bringing up on his own that he'd like to try kayaking next term if it's "
            "an option. Noticeably more talkative, more willing to ask for help when "
            "he wanted it, and generally more settled across the whole four hours than "
            "that first session."
        ),
    },
]


def build_fields(report: dict, profile_item_id: str) -> dict:
    return {
        "profile-2": [int(profile_item_id)],
        "date-of-session": {"start_date": report["date"].isoformat()},
        "title": report["text"],
    }


def create_item(access_token: str, app_id: str, fields: dict) -> dict:
    resp = httpx.post(
        f"{API_BASE}/item/app/{app_id}/",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"fields": fields},
    )
    if resp.status_code not in (200, 201):
        print(f"Create failed ({resp.status_code}). Raw response body:")
        print(resp.text)
        resp.raise_for_status()
    return resp.json()


def main() -> None:
    load_dotenv()
    client_id = require_env("PODIO_CLIENT_ID")
    client_secret = require_env("PODIO_CLIENT_SECRET")
    redirect_uri = os.environ.get("PODIO_REDIRECT_URI", "http://localhost:8080/callback")
    app_id = require_env("PODIO_REPORT_APP_ID")
    profile_item_id = require_env("PODIO_PROFILE_ITEM_ID")

    access_token = get_access_token(client_id, client_secret, redirect_uri)

    print("Creating one test item to verify the write shapes for 'date' and 'app' fields...")
    test_fields = build_fields(REPORTS[0], profile_item_id)
    print(json.dumps(test_fields, indent=2))
    result = create_item(access_token, app_id, test_fields)
    print(f"\nCreated item_id={result.get('item_id')} title={result.get('title')!r}")

    remaining = len(REPORTS) - 1
    answer = input(f"\nTest item looks right? Create the remaining {remaining} reports? [y/N] ")
    if answer.strip().lower() != "y":
        print("Stopping here — only the test item was created.")
        return

    for report in REPORTS[1:]:
        fields = build_fields(report, profile_item_id)
        result = create_item(access_token, app_id, fields)
        print(f"Created item_id={result.get('item_id')} date={report['date']}")


if __name__ == "__main__":
    main()
