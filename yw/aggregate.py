"""
Aggregation of verified extractions into proposals (spec §5 "Aggregation, not
per-sentence proposals", §13 step sequence 4 and part of 5). Plain data transformation:
no model call, no I/O.

One Proposal per (child, target field), across that child's whole report
history in this run. Never one per extracted sentence.

The proposed value is built from the extracted values themselves, not
written by a model. Values that differ only in case, whitespace or trailing
punctuation count as the same phrase. Phrases are ranked by how many distinct
sessions support them, then by how recently. The top few are joined into
the draft. So every phrase in a proposal is something the model extracted
*and* verify.py tied to a quote in a report (spec §2 constraint 3). A
model-written summary would add text that no quote supports. It also keeps
the reports' own wording, which is the §5 voice target: organisational
register, not generic professional language. The drafts are short and a
little rough. That is on purpose: easy to change, not easy to accept (§1, §3).

A proposal cites only the evidence behind the phrases it includes, so
`evidence-count` ("9 of 15 sessions") describes the draft the reviewer is
reading, not observations that were cut from it.
"""

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date

from yw.config import MODEL_NAME, PROMPT_VERSION
from yw.models import Evidence, Extraction, Proposal, Report, TargetField

# Keep drafts short (spec §1): a few phrases invite an edit, a list of ten
# gets waved through or ignored.
MAX_PHRASES = 3

PHRASE_SEPARATOR = "; "

_WHITESPACE = re.compile(r"\s+")
_TRAILING_PUNCTUATION = ".,;:!"

Key = tuple[int, TargetField]  # (child_id, field)


@dataclass
class _Phrase:
    """Every extraction whose value normalises to the same key."""

    extractions: list[Extraction] = field(default_factory=list)

    @property
    def report_ids(self) -> set[int]:
        return {e.report_id for e in self.extractions}

    @property
    def latest(self) -> date:
        return max(e.session_date for e in self.extractions)

    @property
    def display(self) -> str:
        """
        The most common spelling among the extractions; the first seen on a
        tie. Trailing punctuation is dropped so phrases join cleanly.
        """
        counts: dict[str, int] = {}
        for e in self.extractions:
            spelling = _tidy(e.value)
            counts[spelling] = counts.get(spelling, 0) + 1
        return max(counts, key=counts.__getitem__)


def normalise_value(value: str) -> str:
    """
    Comparison key for a value: casefolded, whitespace collapsed, trailing
    punctuation dropped. Used only to group and compare values, never shown.
    """
    return _tidy(value).casefold()


def _tidy(value: str) -> str:
    """Whitespace collapsed, trailing punctuation dropped. Case kept."""
    return _WHITESPACE.sub(" ", value).strip().rstrip(_TRAILING_PUNCTUATION).strip()


def group(
    extractions: Iterable[Extraction],
    reports: Iterable[Report],
    *,
    model_version: str = MODEL_NAME,
    prompt_version: str = PROMPT_VERSION,
) -> list[Proposal]:
    """
    Aggregate verified extractions into one Proposal per (child, field).

    `reports` is every report considered in this run, including those that
    yielded nothing. It sets the "of N sessions" denominator per child.
    Every extraction must come from one of these reports and match its
    child. Anything else means provenance broke upstream, so it raises
    rather than guessing.

    Fields with no surviving extractions produce no proposal. Output is
    ordered by child_id, then by field in TargetField order.
    """
    reports_by_id = {r.item_id: r for r in reports}
    sessions_per_child: dict[int, int] = defaultdict(int)
    for r in reports_by_id.values():
        sessions_per_child[r.child_id] += 1

    phrases: dict[Key, dict[str, _Phrase]] = defaultdict(lambda: defaultdict(_Phrase))
    for e in extractions:
        report = reports_by_id.get(e.report_id)
        if report is None:
            raise ValueError(f"extraction cites report {e.report_id}, which was not considered")
        if report.child_id != e.child_id:
            raise ValueError(f"extraction from report {e.report_id} has the wrong child_id")
        key = normalise_value(e.value)
        if key:
            phrases[(e.child_id, e.field)][key].extractions.append(e)

    field_order = list(TargetField)
    proposals = []
    for child_id, target in sorted(phrases, key=lambda k: (k[0], field_order.index(k[1]))):
        chosen = _rank(phrases[(child_id, target)].values())[:MAX_PHRASES]
        proposals.append(
            Proposal(
                child_id=child_id,
                field=target,
                proposed_value=PHRASE_SEPARATOR.join(p.display for p in chosen),
                evidence=_evidence(chosen),
                sessions_considered=sessions_per_child[child_id],
                model_version=model_version,
                prompt_version=prompt_version,
            )
        )
    return proposals


def drop_current(
    proposals: Iterable[Proposal],
    child_id: int,
    current_values: Mapping[TargetField, str],
) -> list[Proposal]:
    """
    Step 5 of the spec §13 sequence, in part. Drop a proposal whose value
    matches what's already on the child's profile. Values are compared with
    normalise_value, so a difference in case or a trailing full stop doesn't
    count as a new value.

    A run covers one child, so `current_values` is that child's
    profile, keyed by field; a missing field means the profile has nothing
    there. A proposal for any other child raises an error, so one child's
    profile can never be compared against another child's proposals.

    Not done yet (MVP): dropping values previously rejected for this child
    (spec §5 batch run safety rule 2). That needs another Podio query, to the
    proposals app, for this child's `rejected` items. Deferred, see spec §5.
    """
    kept = []
    for p in proposals:
        if p.child_id != child_id:
            raise ValueError(f"proposal for child {p.child_id} in a run for child {child_id}")
        current = current_values.get(p.field)
        if current is not None and normalise_value(current) == normalise_value(p.proposed_value):
            continue
        kept.append(p)
    return kept


def _rank(phrases: Iterable[_Phrase]) -> list[_Phrase]:
    """Most distinct sessions first, then most recent (keep the profile current)."""
    return sorted(phrases, key=lambda p: (len(p.report_ids), p.latest), reverse=True)


def _evidence(chosen: Iterable[_Phrase]) -> tuple[Evidence, ...]:
    """
    The quotes behind the chosen phrases, oldest session first, then in
    report order. Deduplicated, because two values can cite the same
    sentence.
    """
    extractions = sorted(
        (e for p in chosen for e in p.extractions),
        key=lambda e: (e.session_date, e.report_id, e.start),
    )
    return tuple(dict.fromkeys(e.evidence for e in extractions))
