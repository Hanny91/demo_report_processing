from datetime import date

import pytest

from yw.models import Evidence, Proposal, TargetField
from yw.podio.write import (
    WRITTEN_FIELDS,
    ProposalsApp,
    ProposalsAppError,
    build_fields,
    create_proposal,
    load_proposals_app,
)

APP_ID = 30822495
CHILD = 3352054230

TARGET_OPTIONS = {"presentation": 1, "boundaries": 2, "triggers": 3, "projects-and-activities": 4}
STATUS_OPTIONS = {"proposed": 11, "accepted": 12, "edited": 13, "rejected": 14}


class FakeClient:
    def __init__(self, get_response=None, post_response=None):
        self.get_response = get_response
        self.post_response = post_response
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(("GET", path, None))
        return self.get_response

    def post(self, path, body):
        self.calls.append(("POST", path, body))
        return self.post_response


def category(external_id, options, deleted=()):
    return {
        "external_id": external_id,
        "type": "category",
        "status": "active",
        "config": {"settings": {"options": [
            {"id": i, "text": t, "status": "deleted" if t in deleted else "active"} for t, i in options.items()
        ]}},
    }


def app_definition(skip=(), extra_fields=(), deleted_status_options=()):
    fields = [
        {"external_id": name, "type": "text", "status": "active"}
        for name in WRITTEN_FIELDS
        if name not in ("target-field", "status") and name not in skip
    ]
    if "target-field" not in skip:
        fields.append(category("target-field", TARGET_OPTIONS))
    if "status" not in skip:
        fields.append(category("status", STATUS_OPTIONS, deleted=deleted_status_options))
    return {"app_id": APP_ID, "fields": fields + list(extra_fields)}


APP = ProposalsApp(app_id=APP_ID, target_field_options=TARGET_OPTIONS, status_options=STATUS_OPTIONS)


def proposal(**overrides):
    args = dict(
        child_id=CHILD,
        field=TargetField.TRIGGERS,
        proposed_value="frustration after repeated failure",
        evidence=(
            Evidence(report_id=11, session_date=date(2026, 8, 7), quote="threw the compass down"),
            Evidence(report_id=12, session_date=date(2026, 8, 14), quote="stopped & walked off"),
        ),
        sessions_considered=10,
        model_version="llama3.1:8b",
        prompt_version="v1",
    )
    args.update(overrides)
    return Proposal(**args)


# --- load_proposals_app ------------------------------------------------------


def test_app_is_loaded_with_option_ids_by_text():
    app = load_proposals_app(FakeClient(get_response=app_definition()), APP_ID)
    assert app == APP


def test_missing_field_is_refused():
    with pytest.raises(ProposalsAppError, match="evidence-quotes"):
        load_proposals_app(FakeClient(get_response=app_definition(skip=("evidence-quotes",))), APP_ID)


def test_deleted_field_counts_as_missing():
    definition = app_definition(skip=("prompt-version",))
    definition["fields"].append({"external_id": "prompt-version", "type": "text", "status": "deleted"})
    with pytest.raises(ProposalsAppError, match="prompt-version"):
        load_proposals_app(FakeClient(get_response=definition), APP_ID)


def test_deleted_proposed_option_is_refused():
    # Stage 4: a corrupted status field had all its options flipped to deleted.
    definition = app_definition(deleted_status_options=("proposed",))
    with pytest.raises(ProposalsAppError, match="proposed"):
        load_proposals_app(FakeClient(get_response=definition), APP_ID)


# --- build_fields / create_proposal --------------------------------------------


def test_fields_in_podio_write_shape():
    fields = build_fields(proposal(), APP)

    assert "title" not in fields  # the app has no title field
    assert fields["child"] == [CHILD]
    assert fields["target-field"] == TARGET_OPTIONS["triggers"]
    assert fields["evidence-count"] == 2
    assert fields["source-reports"] == [11, 12]
    assert fields["model-version"] == "llama3.1:8b"
    assert fields["prompt-version"] == "v1"


def test_status_is_always_proposed():
    assert build_fields(proposal(), APP)["status"] == STATUS_OPTIONS["proposed"]


def test_reviewer_fields_are_left_empty():
    fields = build_fields(proposal(), APP)
    for name in ("final-value", "reviewed-by", "reviewed-at", "review-duration"):
        assert name not in fields


def test_evidence_quotes_one_paragraph_each_and_escaped():
    quotes = build_fields(proposal(), APP)["evidence-quotes"]
    assert quotes == (
        '<p>2026-08-07 — "threw the compass down"</p>'
        '<p>2026-08-14 — "stopped &amp; walked off"</p>'
    )


def test_proposed_value_is_escaped():
    fields = build_fields(proposal(proposed_value="calm <mostly>"), APP)
    assert fields["proposed-value"] == "calm &lt;mostly&gt;"


def test_create_proposal_posts_one_item_and_returns_its_id():
    client = FakeClient(post_response={"item_id": 555})

    assert create_proposal(client, APP, proposal()) == 555
    [(method, path, body)] = client.calls
    assert (method, path) == ("POST", f"/item/app/{APP_ID}/")
    assert body == {"fields": build_fields(proposal(), APP)}
