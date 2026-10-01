from datetime import date

from yw import run
from yw.extract.llm import LLMError
from yw.models import Evidence, Proposal, Report, TargetField

TEXT = "Climbed the wall twice. Got frustrated and threw the rope down."
REPORT = Report(item_id=11, child_id=1, session_date=date(2026, 8, 7), text=TEXT)


def test_extract_keeps_verified_quotes_and_counts_the_rest(monkeypatch):
    replies = {
        TargetField.PRESENTATION: '{"extractions": []}',
        TargetField.BOUNDARIES: "not json at all",
        TargetField.TRIGGERS: (
            '{"extractions": [{"value": "frustration", "quote": "Got frustrated and threw the rope down."},'
            ' {"value": "invented", "quote": "Shouted at staff."}]}'
        ),
    }

    def fake_complete(messages):
        for field, reply in replies.items():
            if f"Field: {field.value}" in messages[1]["content"]:
                return reply
        raise LLMError("down")

    monkeypatch.setattr(run, "complete", fake_complete)

    kept, processed, counts = run.extract([REPORT])

    assert [(e.field, e.quote) for e in kept] == [
        (TargetField.TRIGGERS, "Got frustrated and threw the rope down.")
    ]
    assert processed == [REPORT]
    assert counts == {"calls": 4, "llm_errors": 1, "unparseable": 1, "rejected": 1}


def test_consecutive_failures_stop_extraction_and_drop_the_report_in_progress(monkeypatch):
    reports = [REPORT, Report(item_id=12, child_id=1, session_date=date(2026, 8, 14), text=TEXT)]
    calls = []

    def fake_complete(messages):
        calls.append(1)
        if len(calls) <= len(TargetField):  # the whole first report works
            return '{"extractions": [{"value": "v", "quote": "Climbed the wall twice."}]}'
        raise LLMError("down")

    monkeypatch.setattr(run, "complete", fake_complete)

    kept, processed, counts = run.extract(reports)

    assert processed == [REPORT]
    assert {e.report_id for e in kept} == {11}
    assert counts["llm_errors"] == run.MAX_CONSECUTIVE_FAILURES
    assert counts["calls"] == len(TargetField) + run.MAX_CONSECUTIVE_FAILURES


def test_saved_run_round_trips(tmp_path):
    p = Proposal(
        child_id=1,
        field=TargetField.TRIGGERS,
        proposed_value="frustration — “rope”",
        evidence=(Evidence(report_id=11, session_date=date(2026, 8, 7), quote="threw the rope down"),),
        sessions_considered=10,
        model_version="m",
        prompt_version="v1",
    )
    path = tmp_path / "runs" / "saved.json"

    run.write_saved(path, [{"proposal": p, "item_id": None}, {"proposal": p, "item_id": 42}])

    assert run.load_saved(path) == [{"proposal": p, "item_id": None}, {"proposal": p, "item_id": 42}]
