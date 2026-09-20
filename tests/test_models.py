from datetime import date

import pytest

from yw.models import Evidence, Extraction, Proposal, TargetField


def ev(report_id: int, quote: str = "a quote", day: int = 1) -> Evidence:
    return Evidence(report_id=report_id, session_date=date(2026, 7, day), quote=quote)


def proposal(**overrides) -> Proposal:
    args = dict(
        child_id=3352054230,
        field=TargetField.PRESENTATION,
        proposed_value="More talkative since July; starts conversations himself.",
        evidence=(ev(11), ev(12), ev(13)),
        sessions_considered=10,
        model_version="test-model",
        prompt_version="p0",
    )
    args.update(overrides)
    return Proposal(**args)


def extraction(**overrides) -> Extraction:
    args = dict(
        report_id=1, child_id=2, session_date=date(2026, 7, 3),
        field=TargetField.TRIGGERS, value="v", quote="abc", start=0, end=3,
    )
    args.update(overrides)
    return Extraction(**args)


def test_target_field_values_match_podio_external_ids():
    assert {f.value for f in TargetField} == {
        "presentation",
        "boundaries",
        "triggers",
        "projects-and-activities",
    }


# --- Proposal ----------------------------------------------------------------


def test_evidence_count_is_number_of_distinct_reports():
    p = proposal(evidence=(ev(11, "one"), ev(11, "two"), ev(12)))

    assert p.evidence_count == 2
    assert p.source_report_ids == (11, 12)


def test_source_report_ids_keep_first_appearance_order():
    p = proposal(evidence=(ev(13), ev(11), ev(13, "again"), ev(12)))
    assert p.source_report_ids == (13, 11, 12)


def test_proposal_without_evidence_is_refused():
    with pytest.raises(ValueError, match="at least one source"):
        proposal(evidence=())


def test_proposal_with_duplicate_evidence_is_refused():
    with pytest.raises(ValueError, match="duplicates"):
        proposal(evidence=(ev(11), ev(11)))


def test_proposal_cannot_cite_more_reports_than_considered():
    with pytest.raises(ValueError, match="sessions_considered"):
        proposal(evidence=(ev(1), ev(2), ev(3)), sessions_considered=2)


def test_proposal_repr_hides_value_and_quotes():
    p = proposal(evidence=(ev(11, "threw the compass"),))
    assert "talkative" not in repr(p)
    assert "compass" not in repr(p)


# --- Extraction --------------------------------------------------------------


def test_extraction_evidence_carries_report_date_and_quote():
    e = extraction().evidence
    assert (e.report_id, e.session_date, e.quote) == (1, date(2026, 7, 3), "abc")


def test_extraction_offsets_must_match_quote():
    with pytest.raises(ValueError, match="length"):
        extraction(quote="abc", start=0, end=5)


@pytest.mark.parametrize("start,end", [(-1, 2), (3, 3), (5, 2)])
def test_extraction_rejects_invalid_offsets(start, end):
    with pytest.raises(ValueError, match="offsets"):
        extraction(quote="x" * max(end - start, 0), start=start, end=end)
