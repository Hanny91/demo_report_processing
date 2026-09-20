"""
Plain data passed between pipeline steps. No I/O, no model calls.

Report text and extracted values are sensitive (spec §2, §5: never log field
values or report text), so every field that holds them is excluded from
repr(). Logging or printing one of these objects shows IDs only.
"""

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


class TargetField(StrEnum):
    """
    Profile fields a proposal can target — the closed list in spec §6.

    Values double as the `external_id` of the matching field on the Child
    Profile app and as the option names of `target-field` on Suggested
    Profile Updates.
    """

    PRESENTATION = "presentation"
    BOUNDARIES = "boundaries"
    TRIGGERS = "triggers"
    PROJECTS_AND_ACTIVITIES = "projects-and-activities"


@dataclass(frozen=True)
class Report:
    """One daily report, read from Podio for the duration of one run."""

    item_id: int
    child_id: int
    session_date: date
    text: str = field(repr=False)


@dataclass(frozen=True)
class RawExtraction:
    """
    What the model claims, before verification. Untrusted: `quote` may be
    paraphrased or invented, which is exactly what verify.py checks.
    """

    field: TargetField
    value: str = field(repr=False)
    quote: str = field(repr=False)


@dataclass(frozen=True)
class Extraction:
    """
    A verified extraction: `quote` is an exact substring of the report with
    item_id `report_id`, at text[start:end]. Only verify.py should build
    these. report_id is required, so provenance can't be lost on the way to
    aggregation (spec §2, constraint 3).
    """

    report_id: int
    child_id: int
    session_date: date
    field: TargetField
    value: str = field(repr=False)
    quote: str = field(repr=False)
    start: int
    end: int

    def __post_init__(self) -> None:
        if not 0 <= self.start < self.end:
            raise ValueError(f"invalid quote offsets {self.start}:{self.end}")
        if self.end - self.start != len(self.quote):
            raise ValueError("quote offsets don't match quote length")

    @property
    def evidence(self) -> "Evidence":
        return Evidence(report_id=self.report_id, session_date=self.session_date, quote=self.quote)


@dataclass(frozen=True)
class Evidence:
    """
    One verified quote supporting a proposal: shown to the reviewer in
    `evidence-quotes` (spec §6). `quote` is the report's own text, sliced
    by verify.py, never the model's retyping of it.
    """

    report_id: int
    session_date: date
    quote: str = field(repr=False)


@dataclass(frozen=True)
class Proposal:
    """
    One proposed value for one (child, target field), aggregated across the
    child's reports. Written to Suggested Profile Updates by podio/write.py,
    always with status `proposed` (spec §6).

    `evidence` holds every supporting quote, possibly several from the same
    report. `source_report_ids` and `evidence_count` are derived from it, so
    the links, the count and the quotes can't disagree.
    """

    child_id: int
    field: TargetField
    proposed_value: str = field(repr=False)
    evidence: tuple[Evidence, ...]
    sessions_considered: int
    model_version: str
    prompt_version: str

    def __post_init__(self) -> None:
        if not self.evidence:
            raise ValueError("a proposal must cite at least one source report")
        if len(set(self.evidence)) != len(self.evidence):
            raise ValueError("evidence contains duplicates")
        if self.sessions_considered < self.evidence_count:
            raise ValueError("sessions_considered is less than the number of sources")

    @property
    def source_report_ids(self) -> tuple[int, ...]:
        """Distinct supporting reports, in order of first appearance."""
        return tuple(dict.fromkeys(e.report_id for e in self.evidence))

    @property
    def evidence_count(self) -> int:
        """Supporting reports, read as '<evidence_count> of <sessions_considered>'."""
        return len(self.source_report_ids)
