from datetime import date

import pytest

from yw.extract.verify import verify_quote
from yw.models import RawExtraction, Report, TargetField

# Mock report, from seed_reports.py (2026-08-07).
TEXT = (
    "Orienteering with a map and compass around the site today, a new "
    "activity for him. Worked out two of the three checkpoints unaided, "
    "including correcting himself once when he realised he'd misread the "
    "bearing. Got visibly frustrated at the third checkpoint after several "
    "failed attempts, threw the compass down onto the grass and said he "
    "wanted to stop. Gave him a couple of minutes rather than stepping in "
    "immediately; he calmed down on his own and came back to finish the round "
    "with some support from me on that last leg."
)
REPORT = Report(item_id=3352099001, child_id=3352054230, session_date=date(2026, 8, 7), text=TEXT)

EXACT = (
    "Got visibly frustrated at the third checkpoint after several failed "
    "attempts, threw the compass down onto the grass and said he wanted to stop."
)


def raw(quote: str, field: TargetField = TargetField.TRIGGERS) -> RawExtraction:
    return RawExtraction(field=field, value="Frustration after repeated failure", quote=quote)


# --- accepted --------------------------------------------------------------


def test_exact_quote_is_accepted_with_offsets_into_the_report():
    result = verify_quote(raw(EXACT), REPORT)

    assert result is not None
    assert TEXT[result.start : result.end] == EXACT
    assert result.quote == EXACT


def test_provenance_comes_from_the_report_not_the_model():
    result = verify_quote(raw(EXACT), REPORT)

    assert result.report_id == REPORT.item_id
    assert result.child_id == REPORT.child_id


def test_field_and_value_carry_through_unchanged():
    extraction = RawExtraction(
        field=TargetField.BOUNDARIES, value="Needs a few minutes of space", quote=EXACT
    )
    result = verify_quote(extraction, REPORT)

    assert result.field is TargetField.BOUNDARIES
    assert result.value == "Needs a few minutes of space"


def test_partial_sentence_that_appears_verbatim_is_accepted():
    quote = "he calmed down on his own"
    result = verify_quote(raw(quote), REPORT)

    assert result is not None
    assert TEXT[result.start : result.end] == quote


def test_quote_at_very_start_and_very_end_of_report():
    first = "Orienteering with a map"
    last = "support from me on that last leg."

    assert verify_quote(raw(first), REPORT).start == 0
    assert verify_quote(raw(last), REPORT).end == len(TEXT)


def test_repeated_quote_uses_first_occurrence():
    report = Report(item_id=1, child_id=2, session_date=date(2026, 6, 1), text="Calm. Then calm.")
    result = verify_quote(raw("alm"), report)

    assert (result.start, result.end) == (1, 4)


# --- rejected: paraphrase and invention -------------------------------------


def test_paraphrase_is_rejected():
    paraphrase = "He got frustrated at the third checkpoint and threw the compass on the grass."
    assert verify_quote(raw(paraphrase), REPORT) is None


def test_invented_observation_is_rejected():
    assert verify_quote(raw("He refused to wear the helmet."), REPORT) is None


def test_quote_from_a_different_report_is_rejected():
    # Real sentence from the 2026-06-12 seed report, not this one.
    other = "didn't want to discuss it"
    assert verify_quote(raw(other), REPORT) is None


def test_quote_extending_past_the_real_text_is_rejected():
    assert verify_quote(raw(EXACT + " He sulked for the rest of the day."), REPORT) is None


# --- accepted: typography differs, wording doesn't ---------------------------

# Mock report in the seed reports' style: em dash, curly quotes, ellipsis.
TYPO_TEXT = (
    "Wary and withdrawn from the start — didn’t want to talk. "
    "Said “leave it” when I offered help… then came back on his own."
)
TYPO = Report(item_id=7, child_id=2, session_date=date(2026, 6, 12), text=TYPO_TEXT)


@pytest.mark.parametrize(
    "quote",
    [
        "Wary and withdrawn from the start - didn't want to talk.",
        "Wary and withdrawn from the start -- didn't want to talk.".replace("--", "–"),
        "Wary and withdrawn from the start — didn’t want to talk.",
        'Said "leave it" when I offered help... then came back',
        "Said “leave it” when I offered help… then came back",
        "Wary and withdrawn  from the start - didn't\nwant to talk.",
    ],
    ids=["ascii-dash-apostrophe", "en-dash", "identical", "straight-quotes-dots", "curly", "whitespace"],
)
def test_typographic_differences_are_accepted(quote):
    assert verify_quote(raw(quote), TYPO) is not None


def test_stored_quote_is_the_reports_own_text_not_the_models():
    result = verify_quote(raw("Wary and withdrawn from the start - didn't want to talk."), TYPO)

    assert result.quote == "Wary and withdrawn from the start — didn’t want to talk."
    assert TYPO_TEXT[result.start : result.end] == result.quote


def test_ellipsis_maps_back_to_the_single_original_character():
    result = verify_quote(raw('when I offered help... then'), TYPO)

    assert result.quote == "when I offered help… then"
    assert TYPO_TEXT[result.start : result.end] == result.quote


def test_quote_ending_on_a_typographic_character_includes_it():
    result = verify_quote(raw("Said \"leave it\""), TYPO)
    assert result.quote == "Said “leave it”"


def test_ascii_report_matches_curly_quote_from_model():
    # Symmetric: normalisation applies to both sides.
    assert verify_quote(raw("he’d misread the bearing"), REPORT).quote == "he'd misread the bearing"


# --- rejected: only typography is forgiven ------------------------------------


def test_case_difference_is_rejected():
    assert verify_quote(raw(EXACT.lower()), REPORT) is None


def test_missing_punctuation_is_rejected():
    assert verify_quote(raw(EXACT.replace("attempts,", "attempts")), REPORT) is None


def test_added_punctuation_is_rejected():
    assert verify_quote(raw("he calmed down, on his own"), REPORT) is None


def test_dash_where_the_report_has_a_space_is_rejected():
    assert verify_quote(raw("third-checkpoint"), REPORT) is None


def test_single_word_change_is_rejected():
    assert verify_quote(raw(EXACT.replace("visibly", "very")), REPORT) is None


@pytest.mark.parametrize("wrapped", [f'"{EXACT}"', f"'{EXACT}'", f"“{EXACT}”"])
def test_model_output_noise_around_quote_is_rejected(wrapped):
    # Stripping model-output noise is parse.py's job, not verify's.
    assert verify_quote(raw(wrapped), REPORT) is None


# --- rejected: degenerate quotes --------------------------------------------


@pytest.mark.parametrize("quote", ["", " ", "\n\t"])
def test_empty_or_whitespace_quote_is_rejected(quote):
    # "" is a substring of every string; it must never count as a match.
    assert verify_quote(raw(quote), REPORT) is None


def test_empty_report_rejects_everything():
    report = Report(item_id=1, child_id=2, session_date=date(2026, 6, 1), text="")
    assert verify_quote(raw("anything"), report) is None


# --- nothing sensitive in repr ------------------------------------------------


def test_repr_of_extraction_contains_no_report_text_or_value():
    result = verify_quote(raw(EXACT), REPORT)
    text = repr(result) + repr(REPORT)

    assert "compass" not in text
    assert "Frustration" not in text
    assert str(REPORT.item_id) in text
