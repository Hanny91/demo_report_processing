"""
Quote verification — the choke point the provenance design rests on (spec §2
constraint 3, §5, §13).

The model returns the sentence it believes supports its extraction. If that
quote is not in the report text, the extraction is discarded. Not flagged,
not lowered in confidence: discarded.

Matching is exact after *typographic* normalisation only (decided 2026-09-18,
spec §5): dash variants, curly vs straight quote marks, "…" vs "...", and
runs of whitespace are treated as equal, because a model retyping "—" as "-"
hasn't changed what the report says. Everything else must match exactly —
case, punctuation, every word. No fuzzy matching.

The stored quote is always sliced from the original report text, never taken
from the model. So what a reviewer sees as evidence is character-for-
character what the instructor wrote, whatever typography the model used.

Cleaning up the model's output format (surrounding quote marks, stray
whitespace) is parse.py's job, before a quote reaches this function.
"""

from yw.models import Extraction, RawExtraction, Report

# Each maps to one ASCII equivalent. Deliberately short: typography only.
_TYPOGRAPHIC = {
    "‐": "-",  # hyphen
    "‑": "-",  # non-breaking hyphen
    "‒": "-",  # figure dash
    "–": "-",  # en dash
    "—": "-",  # em dash
    "―": "-",  # horizontal bar
    "−": "-",  # minus sign
    "‘": "'",  # left single quote
    "’": "'",  # right single quote / apostrophe
    "‚": "'",  # single low-9 quote
    "′": "'",  # prime
    "“": '"',  # left double quote
    "”": '"',  # right double quote
    "„": '"',  # double low-9 quote
    "″": '"',  # double prime
    "…": "...",  # ellipsis
}


def verify_quote(raw: RawExtraction, report: Report) -> Extraction | None:
    """
    Return a verified Extraction if raw.quote appears in the report (exactly,
    up to typography), otherwise None.

    Takes the whole Report rather than just its text so the result carries
    the report's item_id and date — provenance comes from the report the
    quote was matched against, never from anything the model said.

    If the quote occurs more than once, the first occurrence is used;
    provenance is the same report either way.
    """
    if not raw.quote or raw.quote.isspace():
        # An empty string is a substring of everything, so it would always
        # "match". It supports nothing.
        return None

    text = report.text
    norm_text, origin = normalise(text)
    norm_quote, _ = normalise(raw.quote)

    pos = norm_text.find(norm_quote)
    if pos == -1:
        return None

    start = origin[pos]
    end = origin[pos + len(norm_quote) - 1] + 1

    return Extraction(
        report_id=report.item_id,
        child_id=report.child_id,
        session_date=report.session_date,
        field=raw.field,
        value=raw.value,
        quote=text[start:end],
        start=start,
        end=end,
    )


def normalise(text: str) -> tuple[str, list[int]]:
    """
    Typographically normalise text. Returns the normalised string and, for
    each of its characters, the index in `text` it came from — so a match in
    the normalised text can be mapped back to a slice of the original.
    """
    out: list[str] = []
    origin: list[int] = []
    in_space = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if not in_space:
                out.append(" ")
                origin.append(i)
            in_space = True
            continue
        in_space = False
        for c in _TYPOGRAPHIC.get(ch, ch):
            out.append(c)
            origin.append(i)
    return "".join(out), origin
