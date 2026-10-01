from datetime import date

import pytest

from yw.aggregate import MAX_PHRASES, drop_current, group, normalise_value
from yw.models import Extraction, Report, TargetField

CHILD = 3352054230
OTHER_CHILD = 999
P = TargetField.PRESENTATION
T = TargetField.TRIGGERS


def report(item_id: int, day: int = 1, child_id: int = CHILD) -> Report:
    return Report(item_id=item_id, child_id=child_id, session_date=date(2026, 7, day), text="x" * 200)


def ext(report_id: int, value: str, field=P, day: int = 1, quote: str = "a quote",
        start: int = 0, child_id: int = CHILD) -> Extraction:
    return Extraction(
        report_id=report_id, child_id=child_id, session_date=date(2026, 7, day),
        field=field, value=value, quote=quote, start=start, end=start + len(quote),
    )


REPORTS = [report(i, day=i) for i in range(1, 11)]


def test_one_proposal_per_child_and_field():
    extractions = [
        ext(1, "chatty", day=1), ext(2, "chatty", day=2),
        ext(1, "loud groups", field=T, day=1),
    ]
    proposals = group(extractions, REPORTS)

    assert [(p.child_id, p.field) for p in proposals] == [(CHILD, P), (CHILD, T)]


def test_no_extractions_no_proposals():
    assert group([], REPORTS) == []


def test_sessions_considered_counts_every_report_for_that_child():
    proposals = group([ext(1, "chatty")], REPORTS + [report(50, child_id=OTHER_CHILD)])

    assert proposals[0].sessions_considered == 10
    assert proposals[0].evidence_count == 1


def test_values_differing_only_in_case_and_punctuation_are_one_phrase():
    extractions = [ext(1, "Chatty.", day=1), ext(2, "chatty", day=2), ext(3, "chatty ", day=3)]
    [p] = group(extractions, REPORTS)

    assert p.proposed_value == "chatty"
    assert p.evidence_count == 3


def test_phrases_ranked_by_sessions_then_recency():
    extractions = [
        ext(1, "quiet", day=1),
        ext(2, "chatty", day=2), ext(3, "chatty", day=3),
        ext(9, "tired", day=9),
    ]
    [p] = group(extractions, REPORTS)

    assert p.proposed_value == "chatty; tired; quiet"


def test_same_value_twice_in_one_report_counts_one_session():
    extractions = [
        ext(1, "quiet", day=1, quote="one", start=0), ext(1, "quiet", day=1, quote="two", start=10),
        ext(5, "chatty", day=5),
    ]
    [p] = group(extractions, REPORTS)

    assert p.proposed_value.startswith("chatty")


def test_draft_is_capped_and_evidence_covers_only_included_phrases():
    extractions = [ext(i, f"phrase {i}", day=i, quote=f"quote {i}") for i in range(1, 6)]
    [p] = group(extractions, REPORTS)

    assert len(p.proposed_value.split("; ")) == MAX_PHRASES
    assert p.evidence_count == MAX_PHRASES
    assert set(p.source_report_ids) == {5, 4, 3}


def test_evidence_is_oldest_first_and_deduplicated():
    extractions = [
        ext(3, "chatty", day=3, quote="same"),
        ext(3, "talkative", day=3, quote="same"),
        ext(1, "chatty", day=1, quote="earlier"),
    ]
    [p] = group(extractions, REPORTS)

    assert [e.quote for e in p.evidence] == ["earlier", "same"]


def test_extraction_from_unconsidered_report_is_refused():
    with pytest.raises(ValueError, match="not considered"):
        group([ext(404, "chatty")], REPORTS)


def test_extraction_with_mismatched_child_is_refused():
    with pytest.raises(ValueError, match="child_id"):
        group([ext(1, "chatty", child_id=OTHER_CHILD)], REPORTS)


def test_versions_are_recorded():
    [p] = group([ext(1, "chatty")], REPORTS, model_version="m", prompt_version="v9")
    assert (p.model_version, p.prompt_version) == ("m", "v9")


def test_normalise_value():
    assert normalise_value("  Needs   a warning.  ") == "needs a warning"


# --- drop_current ------------------------------------------------------------


def proposals():
    return group([ext(1, "chatty"), ext(2, "loud groups", field=T, day=2)], REPORTS)


def test_drop_current_drops_value_already_on_profile():
    kept = drop_current(proposals(), CHILD, {P: "Chatty."})
    assert [p.field for p in kept] == [T]


def test_drop_current_keeps_new_values():
    kept = drop_current(proposals(), CHILD, {P: "quiet"})
    assert len(kept) == 2


def test_drop_current_keeps_everything_when_profile_is_empty():
    assert len(drop_current(proposals(), CHILD, {})) == 2


def test_drop_current_refuses_proposal_for_another_child():
    with pytest.raises(ValueError, match="in a run for child"):
        drop_current(proposals(), OTHER_CHILD, {P: "chatty"})
