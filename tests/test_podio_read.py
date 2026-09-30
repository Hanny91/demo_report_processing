from datetime import date

import pytest

from yw.podio.read import FILTER_LIMIT, fetch_reports_for_child

CHILD = 3352054230
OTHER_CHILD = 999
APP = 30816198


class FakeClient:
    def __init__(self, response: dict):
        self.response = response
        self.calls = []

    def post(self, path, body):
        self.calls.append((path, body))
        return self.response


def item(item_id: int, text="Report text.", day: str | None = "2026-08-07", linked=(CHILD,)) -> dict:
    """A report item in the shape Podio's filter endpoint returns (stage 0)."""
    fields = [
        {"external_id": "title", "type": "text", "values": [{"value": text}] if text else []},
        {
            "external_id": "profile-2",
            "type": "app",
            "values": [{"value": {"item_id": i, "title": "Damian", "app": {"app_id": 1}}} for i in linked],
        },
    ]
    if day:
        fields.append({
            "external_id": "date-of-session",
            "type": "date",
            "values": [{"start": f"{day} 00:00:00", "start_date": day, "start_time": None}],
        })
    return {"item_id": item_id, "app_item_id": item_id % 100, "fields": fields}


def fetch(*items, total=None):
    client = FakeClient({"total": len(items) if total is None else total, "items": list(items)})
    return fetch_reports_for_child(client, APP, CHILD), client


def test_one_filter_call_on_the_relationship_field():
    _, client = fetch(item(1))

    assert client.calls == [
        (f"/item/app/{APP}/filter/", {"filters": {"profile-2": [CHILD]}, "limit": FILTER_LIMIT})
    ]


def test_reports_are_built_from_fields():
    [report], _ = fetch(item(3352099001, text="Orienteering today.", day="2026-08-07"))

    assert report.item_id == 3352099001  # item_id, never app_item_id
    assert report.child_id == CHILD
    assert report.session_date == date(2026, 8, 7)
    assert report.text == "Orienteering today."


def test_reports_are_oldest_first():
    reports, _ = fetch(item(1, day="2026-08-07"), item(2, day="2026-06-01"), item(3, day="2026-07-15"))
    assert [r.item_id for r in reports] == [2, 3, 1]


def test_no_reports_is_an_empty_list():
    reports, _ = fetch()
    assert reports == []


def test_report_for_another_child_is_refused():
    with pytest.raises(ValueError, match="not linked to child"):
        fetch(item(1), item(2, linked=(OTHER_CHILD,)))


def test_report_linked_to_several_profiles_including_this_child_is_kept():
    reports, _ = fetch(item(1, linked=(OTHER_CHILD, CHILD)))
    assert len(reports) == 1


def test_report_without_text_is_refused():
    with pytest.raises(ValueError, match="report 1 has no text"):
        fetch(item(1, text=None))


def test_report_without_date_is_refused():
    with pytest.raises(ValueError, match="report 1 has no session date"):
        fetch(item(1, day=None))


def test_more_reports_than_one_page_is_refused():
    with pytest.raises(ValueError, match="without paging"):
        fetch(item(1), total=FILTER_LIMIT + 1)


def test_errors_name_ids_not_text():
    with pytest.raises(ValueError) as info:
        fetch(item(1, text="threw the compass", linked=(OTHER_CHILD,)))
    assert "compass" not in str(info.value)
