"""
Writing proposals into the Suggested Profile Updates app (build stage 4, spec
§6, §8, §13 step 6). Additive only: this module creates items and never
updates or deletes one. So batch run safety rule 1 (spec §5, never
overwrite an item whose status isn't `proposed`) holds by construction.

Every item is created with `status: proposed`, set explicitly. There is no
field default to fall back on (spec §6, stage 4 provisioning).

Before any write, load_proposals_app reads the app definition once. It
checks every field this module writes exists, and it looks up category
option ids by their text, so nothing here depends on ids that change when
the app is recreated. It runs before the model does, so a missing field
stops the run in seconds, not after an hour of extraction.

Written but never edited: `proposed-value` and `evidence-quotes` (spec §6).
Left for the reviewer: `final-value`, `reviewed-by`, `reviewed-at`,
`review-duration`.

Large text fields hold HTML in Podio. Values are escaped, and each quote
goes in its own paragraph, so a "<" or "&" in a report can't change the
markup.

MVP: the title names the field, the session count and the run date, not the
child. The `child` relationship shows the name in Podio, and fetching it
would cost another call.
"""

import html
from dataclasses import dataclass
from datetime import date

from yw.models import Proposal, TargetField
from yw.podio.client import PodioClient

TITLE = "title"
CHILD = "child"
TARGET_FIELD = "target-field"
EVIDENCE_COUNT = "evidence-count"
PROPOSED_VALUE = "proposed-value"
EVIDENCE_QUOTES = "evidence-quotes"
SOURCE_REPORTS = "source-reports"
STATUS = "status"
MODEL_VERSION = "model-version"
PROMPT_VERSION = "prompt-version"

WRITTEN_FIELDS = (
    CHILD, TARGET_FIELD, EVIDENCE_COUNT, PROPOSED_VALUE, EVIDENCE_QUOTES,
    SOURCE_REPORTS, STATUS, MODEL_VERSION, PROMPT_VERSION,
)

STATUS_PROPOSED = "proposed"


class ProposalsAppError(ValueError):
    """The Suggested Profile Updates app doesn't have the fields or options this module writes."""


@dataclass(frozen=True)
class ProposalsApp:
    app_id: int
    target_field_options: dict[str, int]  # option text -> option id
    status_options: dict[str, int]


def load_proposals_app(client: PodioClient, app_id: int) -> ProposalsApp:
    """Read the app definition once, check it, and look up category option ids."""
    app = client.get(f"/app/{app_id}")
    fields = {
        f["external_id"]: f
        for f in app.get("fields", [])
        if f.get("status", "active") == "active" and "external_id" in f
    }

    missing = [name for name in WRITTEN_FIELDS if name not in fields]
    if missing:
        raise ProposalsAppError(
            f"app {app_id} is missing fields {missing}; recreate it with podio_api_work/setup_podio_app.py"
        )

    target_options = _options(fields[TARGET_FIELD])
    status_options = _options(fields[STATUS])

    missing_options = [f.value for f in TargetField if f.value not in target_options]
    if STATUS_PROPOSED not in status_options:
        missing_options.append(STATUS_PROPOSED)
    if missing_options:
        raise ProposalsAppError(f"app {app_id} is missing category options {missing_options}")

    return ProposalsApp(app_id=app_id, target_field_options=target_options, status_options=status_options)


def create_proposal(client: PodioClient, app: ProposalsApp, proposal: Proposal, run_date: date) -> int:
    """Create one proposal item. Returns its item_id."""
    response = client.post(f"/item/app/{app.app_id}/", {"fields": build_fields(proposal, app, run_date)})
    return response["item_id"]


def build_fields(proposal: Proposal, app: ProposalsApp, run_date: date) -> dict:
    """The item's field values, keyed by external_id, in Podio's write shape."""
    return {
        TITLE: title(proposal, run_date),
        CHILD: [proposal.child_id],
        TARGET_FIELD: app.target_field_options[proposal.field.value],
        EVIDENCE_COUNT: proposal.evidence_count,
        PROPOSED_VALUE: html.escape(proposal.proposed_value),
        EVIDENCE_QUOTES: evidence_quotes_html(proposal),
        SOURCE_REPORTS: list(proposal.source_report_ids),
        STATUS: app.status_options[STATUS_PROPOSED],
        MODEL_VERSION: proposal.model_version,
        PROMPT_VERSION: proposal.prompt_version,
    }


def title(proposal: Proposal, run_date: date) -> str:
    """e.g. "presentation — 9 of 45 sessions — 2026-09-30". Carries the "of N" that evidence-count can't."""
    return (
        f"{proposal.field.value} — {proposal.evidence_count} of "
        f"{proposal.sessions_considered} sessions — {run_date.isoformat()}"
    )


def evidence_quotes_html(proposal: Proposal) -> str:
    """One paragraph per quote: 2026-08-07 — "…" (spec §6)."""
    return "".join(
        f'<p>{e.session_date.isoformat()} — "{html.escape(e.quote)}"</p>' for e in proposal.evidence
    )


def _options(field: dict) -> dict[str, int]:
    options = field.get("config", {}).get("settings", {}).get("options", [])
    return {o["text"]: o["id"] for o in options if o.get("status", "active") == "active"}
