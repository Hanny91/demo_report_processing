"""
Reading a child's reports from Podio into Report objects for one run (build
stage 1, spec §8, §13). Nothing is kept after the run (spec §5).

One call per child: POST /item/app/{app_id}/filter/ on the reports app,
filtered on the relationship field that points at the profile (spec §7).
That call returns every field of every matching report. The
/item/{id}/reference/ route mentioned in §7 lists linked items without their
fields, so it would need another call per report.

Field readers live here and not in a separate schema.py. read.py is the only
module that reads Podio fields for now; split them out once a second reader
needs them. Shapes differ by field type, as stage 0 found (spec progress log,
stage 0 point 4):

    text   {"value": "..."}
    date   {"start_date": "2026-08-07", ...}           no "value" key
    app    {"value": {"item_id": ..., ...whole item}}  item_id, not app_item_id

MVP scope:
  - No paging. A child with more reports than FILTER_LIMIT raises an error
    instead of being silently cut short.
  - Current profile values aren't read. The sandbox profile app has none of
    the four target fields yet, so run.py passes {} to
    aggregate.drop_current.
"""

from datetime import date

from yw.models import Report
from yw.podio.client import PodioClient

# Reports app field external_ids (sandbox, spec progress log stage 0 table).
REPORT_TEXT = "title"
REPORT_DATE = "date-of-session"
REPORT_PROFILE = "profile-2"

# ~45 sessions per child per year (spec §4); 500 is Podio's maximum per page.
FILTER_LIMIT = 500


def fetch_reports_for_child(client: PodioClient, report_app_id: int, child_id: int) -> list[Report]:
    """
    Every report linked to `child_id`, oldest session first.

    Each report is checked to be linked to `child_id`. If Podio ignored the
    filter, another child's reports would reach this child's proposals, so
    that raises an error instead. A report missing its text or date also
    raises, naming the item_id only.
    """
    response = client.post(
        f"/item/app/{report_app_id}/filter/",
        {"filters": {REPORT_PROFILE: [child_id]}, "limit": FILTER_LIMIT},
    )

    total = response.get("total", 0)
    if total > FILTER_LIMIT:
        raise ValueError(f"child {child_id} has {total} reports, more than the {FILTER_LIMIT} read without paging")

    reports = [_to_report(item, child_id) for item in response.get("items", [])]
    return sorted(reports, key=lambda r: (r.session_date, r.item_id))


def _to_report(item: dict, child_id: int) -> Report:
    item_id = item["item_id"]
    fields = _fields_by_external_id(item)

    if child_id not in _linked_item_ids(fields.get(REPORT_PROFILE)):
        raise ValueError(f"report {item_id} is not linked to child {child_id}; the filter was not applied")

    text = _text(fields.get(REPORT_TEXT))
    if not text:
        raise ValueError(f"report {item_id} has no text")

    session_date = _date(fields.get(REPORT_DATE))
    if session_date is None:
        raise ValueError(f"report {item_id} has no session date")

    return Report(item_id=item_id, child_id=child_id, session_date=session_date, text=text)


def _fields_by_external_id(item: dict) -> dict[str, dict]:
    return {f["external_id"]: f for f in item.get("fields", []) if "external_id" in f}


def _text(field: dict | None) -> str | None:
    if not field or not field.get("values"):
        return None
    value = field["values"][0].get("value")
    return value if isinstance(value, str) and value.strip() else None


def _date(field: dict | None) -> date | None:
    if not field or not field.get("values"):
        return None
    start = field["values"][0].get("start_date")
    return date.fromisoformat(start) if start else None


def _linked_item_ids(field: dict | None) -> list[int]:
    if not field:
        return []
    return [
        v["value"]["item_id"]
        for v in field.get("values", [])
        if isinstance(v.get("value"), dict) and "item_id" in v["value"]
    ]
