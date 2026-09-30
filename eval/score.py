"""
Offline evaluation harness (spec §13, build order step 6). Runs
cases.yaml's mock reports through the real pipeline —
prompt.build_prompt -> llm.complete -> parse.parse_with_retry ->
verify.verify_quote — and reports counts, not pass/fail.

Weight invented extractions heavily (spec §13): a missed observation is a
small loss, an invented one reaching a child's profile is the failure that
matters. expected_missed and invented are therefore reported separately,
never merged into one score.

Matching a verified extraction against an expected quote goes through
verify.normalise, the same typographic tolerance verify_quote itself uses
(dash variants, curly vs straight quotes, "..." vs "…", whitespace runs) —
anything stricter would fail a correct extraction over formatting verify.py
itself doesn't care about.

Run with: python -m eval.score [--cache]

--cache turns on the one named exception to the no-persistence rule (spec
§5, §13): raw model replies keyed by (report_id, field, prompt_version,
model), cached under eval/.cache/ (gitignored). Mock data only, off by
default, and there is no equivalent flag in run.py — this cache must never
be reachable from the real pipeline.
"""

import argparse
import hashlib
import json
from dataclasses import dataclass, field as dataclass_field
from datetime import date
from pathlib import Path

import yaml

from yw.config import MODEL_NAME, PROMPT_VERSION
from yw.extract.llm import complete
from yw.extract.parse import ParseError, parse_with_retry
from yw.extract.prompt import build_prompt
from yw.extract.verify import normalise, verify_quote
from yw.models import Report, TargetField

CASES_PATH = Path(__file__).parent / "cases.yaml"
CACHE_DIR = Path(__file__).parent / ".cache"


@dataclass
class CaseResult:
    case_id: str
    expected_found: int = 0
    expected_missed: int = 0
    invented: int = 0
    verify_failures: int = 0
    unparseable: bool = False


@dataclass
class EvalResult:
    cases: list[CaseResult] = dataclass_field(default_factory=list)

    def _total(self, attr: str) -> int:
        return sum(getattr(c, attr) for c in self.cases)

    @property
    def expected_found(self) -> int:
        return self._total("expected_found")

    @property
    def expected_missed(self) -> int:
        return self._total("expected_missed")

    @property
    def invented(self) -> int:
        return self._total("invented")

    @property
    def verify_failures(self) -> int:
        return self._total("verify_failures")

    @property
    def unparseable(self) -> int:
        return sum(1 for c in self.cases if c.unparseable)


def load_cases(path: Path = CASES_PATH) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def run_eval(cases: list[dict], use_cache: bool = False) -> EvalResult:
    return EvalResult(cases=[_run_case(i, case, use_cache) for i, case in enumerate(cases)])


def _run_case(index: int, case: dict, use_cache: bool) -> CaseResult:
    case_id = case.get("id", f"case-{index}")
    target_field = TargetField(case["field"])
    report = Report(
        item_id=index,
        child_id=0,
        session_date=date.fromisoformat(case.get("date", "2026-01-01")),
        text=case["report"],
    )
    expected = [_key(q) for q in case.get("expected_quotes", [])]
    result = CaseResult(case_id=case_id)

    messages = build_prompt(report, target_field)
    try:
        raw_extractions = parse_with_retry(
            lambda: _get_reply(report, target_field, messages, use_cache), target_field
        )
    except ParseError:
        result.unparseable = True
        result.expected_missed = len(expected)
        return result

    remaining = list(expected)
    for raw in raw_extractions:
        verified = verify_quote(raw, report)
        if verified is None:
            result.verify_failures += 1
            continue
        key = _key(verified.quote)
        if key in remaining:
            remaining.remove(key)
            result.expected_found += 1
        else:
            result.invented += 1

    result.expected_missed = len(remaining)
    return result


def _key(quote: str) -> str:
    """Typographically normalised, for comparing against verified quotes."""
    text, _ = normalise(quote)
    return text


def _get_reply(report: Report, target_field: TargetField, messages: list[dict], use_cache: bool) -> str:
    if not use_cache:
        return complete(messages)

    CACHE_DIR.mkdir(exist_ok=True)
    key = hashlib.sha256(
        f"{report.item_id}|{target_field.value}|{PROMPT_VERSION}|{MODEL_NAME}".encode()
    ).hexdigest()
    cache_file = CACHE_DIR / f"{key}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))["raw"]

    raw = complete(messages)
    cache_file.write_text(json.dumps({"raw": raw}), encoding="utf-8")
    return raw


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the extraction eval harness against cases.yaml.")
    parser.add_argument(
        "--cache",
        action="store_true",
        help="cache raw model replies under eval/.cache/ (mock data only, dev use — spec §13)",
    )
    args = parser.parse_args()

    cases = load_cases()
    result = run_eval(cases, use_cache=args.cache)

    print(f"{len(cases)} cases")
    print(f"expected extractions found:  {result.expected_found}")
    print(f"expected extractions missed: {result.expected_missed}")
    print(f"invented extractions:        {result.invented}   <- weight this heavily")
    print(f"quote verification failures: {result.verify_failures}")
    print(f"unparseable replies:         {result.unparseable}")

    for c in result.cases:
        flags = []
        if c.invented:
            flags.append(f"invented={c.invented}")
        if c.expected_missed:
            flags.append(f"missed={c.expected_missed}")
        if c.verify_failures:
            flags.append(f"verify_failures={c.verify_failures}")
        if c.unparseable:
            flags.append("unparseable")
        if flags:
            print(f"  {c.case_id}: " + ", ".join(flags))


if __name__ == "__main__":
    main()
