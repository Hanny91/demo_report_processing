from datetime import date

import pytest

from yw.extract.parse import ParseError, clean_quote, parse_response, parse_with_retry
from yw.extract.verify import verify_quote
from yw.models import Report, TargetField

F = TargetField.TRIGGERS

ONE = '{"extractions": [{"value": "Frustration after repeated failure", "quote": "threw the compass down"}]}'


def only(result):
    assert len(result) == 1
    return result[0]


# --- the contract ------------------------------------------------------------


def test_well_formed_reply():
    r = only(parse_response(ONE, F))

    assert r.field is F
    assert r.value == "Frustration after repeated failure"
    assert r.quote == "threw the compass down"


def test_field_comes_from_the_caller_not_the_reply():
    reply = '{"extractions": [{"field": "boundaries", "value": "v", "quote": "q"}]}'
    assert only(parse_response(reply, F)).field is F


def test_multiple_extractions_keep_order():
    reply = '{"extractions": [{"value": "a", "quote": "one"}, {"value": "b", "quote": "two"}]}'
    assert [r.quote for r in parse_response(reply, F)] == ["one", "two"]


# --- "nothing found" is a valid answer, not a failure -------------------------


@pytest.mark.parametrize(
    "reply",
    ['{"extractions": []}', '{"extractions": null}', "[]", "```json\n{\"extractions\": []}\n```"],
)
def test_nothing_found_is_an_empty_list(reply):
    assert parse_response(reply, F) == []


# --- format noise is tolerated -----------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        f"```json\n{ONE}\n```",
        f"```\n{ONE}\n```",
        f"Here is the extraction:\n\n{ONE}\n\nLet me know if you need more.",
        f"Sure! ```json\n{ONE}``` Hope that helps.",
        "{'extractions': [{'value': 'Frustration after repeated failure', 'quote': 'threw the compass down'},]}",
        '[{"value": "Frustration after repeated failure", "quote": "threw the compass down"}]',
        '{"value": "Frustration after repeated failure", "quote": "threw the compass down"}',
    ],
    ids=["fenced-json", "fenced-plain", "prose-around", "fence-inline", "python-literal", "bare-list", "bare-object"],
)
def test_format_noise_is_tolerated(reply):
    r = only(parse_response(reply, F))
    assert (r.value, r.quote) == ("Frustration after repeated failure", "threw the compass down")


def test_extra_keys_are_ignored():
    reply = '{"extractions": [{"value": "v", "quote": "q", "confidence": 0.9}], "notes": "x"}'
    assert only(parse_response(reply, F)).quote == "q"


def test_value_whitespace_is_stripped():
    reply = '{"extractions": [{"value": "  v \\n", "quote": "q"}]}'
    assert only(parse_response(reply, F)).value == "v"


# --- unreadable replies raise, so they get retried ---------------------------


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "   ",
        "None found.",
        "I couldn't find any triggers in this report.",
        '{"extractions": [{"value": "v", "quote": "q"}',  # truncated
        '{"results": []}',
        '{"extractions": "none"}',
        '"just a string"',
        "42",
    ],
)
def test_unreadable_reply_raises(reply):
    with pytest.raises(ParseError):
        parse_response(reply, F)


@pytest.mark.parametrize(
    "item",
    [
        '"a string"',
        '{"quote": "q"}',
        '{"value": "", "quote": "q"}',
        '{"value": "v"}',
        '{"value": "v", "quote": ""}',
        '{"value": "v", "quote": "  \\"\\"  "}',
        '{"value": "v", "quote": 3}',
        '{"value": ["v"], "quote": "q"}',
    ],
)
def test_malformed_item_fails_whole_reply(item):
    good = '{"value": "ok", "quote": "fine"}'
    with pytest.raises(ParseError):
        parse_response(f'{{"extractions": [{good}, {item}]}}', F)


def test_error_message_never_contains_the_reply():
    secret = "difficult morning at home"
    with pytest.raises(ParseError) as exc:
        parse_response(f'{{"extractions": [{{"value": "{secret}"}}]}}', F)
    assert secret not in str(exc.value)


# --- quote cleanup: around the quote only ------------------------------------


@pytest.mark.parametrize(
    "noisy",
    [
        '"he calmed down on his own"',
        "'he calmed down on his own'",
        "“he calmed down on his own”",
        "‘he calmed down on his own’",
        "«he calmed down on his own»",
        "  he calmed down on his own \n",
        "...he calmed down on his own...",
        "…he calmed down on his own…",
        '"...he calmed down on his own..."',
        '... "he calmed down on his own" ...',
    ],
)
def test_clean_quote_strips_noise_around_the_quote(noisy):
    assert clean_quote(noisy) == "he calmed down on his own"


@pytest.mark.parametrize(
    "quote",
    [
        "he'd misread the bearing",
        "the boys' den",
        "'twas fine",
        'said "stop" and left',
        "didn't want to talk — walked off",
        "went from A...to B",
    ],
)
def test_clean_quote_never_changes_the_inside(quote):
    assert clean_quote(quote) == quote


def test_unmatched_quote_marks_are_left_alone():
    assert clean_quote('"he calmed down') == '"he calmed down'


# --- one retry, then give up -------------------------------------------------


def replies(*items):
    calls = []

    def get_reply():
        calls.append(1)
        return items[len(calls) - 1]

    return get_reply, calls


def test_good_first_reply_is_not_retried():
    get_reply, calls = replies(ONE)
    assert len(parse_with_retry(get_reply, F)) == 1
    assert len(calls) == 1


def test_bad_then_good_reply_retries_once():
    get_reply, calls = replies("garbage", ONE)
    assert len(parse_with_retry(get_reply, F)) == 1
    assert len(calls) == 2


def test_two_bad_replies_give_up():
    get_reply, calls = replies("garbage", "more garbage", ONE)
    with pytest.raises(ParseError):
        parse_with_retry(get_reply, F)
    assert len(calls) == 2


def test_nothing_found_is_not_retried():
    get_reply, calls = replies('{"extractions": []}', ONE)
    assert parse_with_retry(get_reply, F) == []
    assert len(calls) == 1


# --- parse feeds verify ------------------------------------------------------


def test_cleaned_quote_then_passes_verification():
    report = Report(
        item_id=1,
        child_id=2,
        session_date=date(2026, 8, 7),
        text="Gave him a couple of minutes; he calmed down on his own and came back.",
    )
    reply = '```json\n{"extractions": [{"value": "Needs space", "quote": "\\"...he calmed down on his own...\\""}]}\n```'

    raw = only(parse_response(reply, TargetField.BOUNDARIES))
    assert verify_quote(raw, report) is not None
