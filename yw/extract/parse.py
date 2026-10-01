"""
Tolerant parsing of the model's reply into RawExtractions (spec §13).

Contract with prompt.py — the model is asked for exactly:

    {"extractions": [{"value": "...", "quote": "..."}, ...]}

with {"extractions": []} meaning "nothing in this report for this field".

Tolerant of *format* noise small models add: code fences, prose before or
after the JSON, Python-style literals (single quotes, trailing commas), a bare
list instead of the wrapping object, a single extraction object instead of a
list. Not tolerant of *schema* problems: a missing or empty value or quote
fails the whole response, so it gets retried rather than half-used.

Two outcomes are kept distinct on purpose:
  - []           the model answered, and found nothing. A valid result.
  - ParseError   the reply couldn't be read. Retry once, then give up.

Quote cleanup only removes noise *around* a quote (whitespace, one pair of
wrapping quote marks, leading/trailing ellipses). Nothing inside a quote is
ever changed: verify.py decides whether it is real, by exact match.
"""

import ast
import json
import re
from typing import Callable

from yw.models import RawExtraction, TargetField

MAX_ATTEMPTS = 2  # the first try plus one retry


class ParseError(ValueError):
    """
    The model's reply couldn't be turned into extractions.

    Messages describe the problem only — never include the reply itself,
    which may contain report text (spec §5: never log report text).
    """


_FENCE = re.compile(r"```[a-zA-Z]*\s*\n?(.*?)```", re.DOTALL)

_QUOTE_PAIRS = {
    '"': '"',
    "'": "'",
    "“": "”",  # “ ”
    "‘": "’",  # ‘ ’
    "«": "»",  # « »
}

_ELLIPSES = ("...", "…")


def parse_response(raw: str, field: TargetField) -> list[RawExtraction]:
    """Parse one model reply for one target field. Raises ParseError."""
    data = _load(raw)
    items = _extraction_list(data)
    return [_to_raw_extraction(item, i, field) for i, item in enumerate(items)]


def parse_with_retry(get_reply: Callable[[], str], field: TargetField) -> list[RawExtraction]:
    """
    Call get_reply() and parse it, retrying once if the reply is unparseable.
    Raises the last ParseError if both attempts fail. No agent loop, no
    feedback to the model: the retry is the same request again.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return parse_response(get_reply(), field)
        except ParseError:
            if attempt == MAX_ATTEMPTS:
                raise
    raise AssertionError("unreachable")


def clean_quote(quote: str) -> str:
    """Strip noise around a quote. Never alters its contents."""
    quote = quote.strip()
    changed = True
    while changed:
        changed = False
        for ellipsis in _ELLIPSES:
            if quote.startswith(ellipsis):
                quote, changed = quote[len(ellipsis) :].strip(), True
            if quote.endswith(ellipsis):
                quote, changed = quote[: -len(ellipsis)].strip(), True
        if len(quote) >= 2 and _QUOTE_PAIRS.get(quote[0]) == quote[-1]:
            quote, changed = quote[1:-1].strip(), True
    return quote


def _load(raw: str) -> object:
    if not isinstance(raw, str) or not raw.strip():
        raise ParseError("empty reply")

    for candidate in _candidates(raw):
        try:
            return json.loads(candidate)
        except ValueError:
            pass
        try:
            # Safe: literal_eval only evaluates literals, never code.
            return ast.literal_eval(candidate)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            pass
    raise ParseError("no JSON object or list found in reply")


def _candidates(raw: str) -> list[str]:
    """Substrings that might be the JSON, most specific first."""
    out = [m.group(1).strip() for m in _FENCE.finditer(raw)]
    text = raw.strip()
    out.append(text)
    for open_, close in (("{", "}"), ("[", "]")):
        start, end = text.find(open_), text.rfind(close)
        if start != -1 and end > start:
            out.append(text[start : end + 1])
    return out


def _extraction_list(data: object) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "extractions" in data:
            items = data["extractions"]
            if items is None:
                return []
            if isinstance(items, list):
                return items
            raise ParseError("'extractions' is not a list")
        if "value" in data or "quote" in data:
            return [data]
        raise ParseError("object has no 'extractions' key")
    raise ParseError(f"reply parsed to {type(data).__name__}, not an object or list")


def _to_raw_extraction(item: object, index: int, field: TargetField) -> RawExtraction:
    if not isinstance(item, dict):
        raise ParseError(f"extraction {index} is not an object")

    value, quote = item.get("value"), item.get("quote")
    if not isinstance(value, str) or not value.strip():
        raise ParseError(f"extraction {index} has no usable 'value'")
    if not isinstance(quote, str):
        raise ParseError(f"extraction {index} has no usable 'quote'")

    quote = clean_quote(quote)
    if not quote:
        raise ParseError(f"extraction {index} has an empty 'quote'")

    return RawExtraction(field=field, value=value.strip(), quote=quote)
