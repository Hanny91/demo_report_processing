"""
One batch run for one child (spec §5, §13 step sequence). The only module
with side effects.

    read      podio/read.fetch_reports_for_child
    extract   per (report, field): build_prompt -> complete -> parse_with_retry -> verify_quote
    aggregate aggregate.group -> aggregate.drop_current
    write     podio/write.create_proposal, only with --write

Usage:
    python -m yw.run --child ID                        dry run: no writes, prints counts
    python -m yw.run --child ID --save                 also save proposals to runs/
    python -m yw.run --child ID --save --write         save, then upload
    python -m yw.run --from-saved runs/FILE --write    upload a saved run, no model calls

Output is counts and item ids only, never report text or extracted values
(spec §5).

--save is a sandbox-only exception to the no-persistence rule (spec §5).
It exists so a long extraction run (~40 model calls, 30–80 minutes on CPU)
isn't lost to a failed upload. The file holds proposals, including verified
quotes, so it holds report text. Mock data only. It goes under runs/, which
is gitignored, and is written only when --save is given. Each proposal's
item_id is written back to the file as soon as it is created, so
re-uploading after a failure skips the ones already in Podio.

MVP scope, recorded in the spec: no check for previously rejected values
(§5 safety rule 2), and no current profile values to compare against (the
sandbox profile has none of the target fields).
"""

import argparse
import json
import os
from datetime import date, datetime
from pathlib import Path

from yw.aggregate import drop_current, group
from yw.config import MODEL_NAME, PROMPT_VERSION
from yw.extract.llm import LLMError, complete
from yw.extract.parse import ParseError, parse_with_retry
from yw.extract.prompt import build_prompt
from yw.extract.verify import verify_quote
from yw.models import Evidence, Extraction, Proposal, Report, TargetField
from yw.podio.client import PodioClient, load_env_file, ENV_PATH, REPO_ROOT
from yw.podio.read import fetch_reports_for_child
from yw.podio.write import create_proposal, load_proposals_app

RUNS_DIR = REPO_ROOT / "runs"


def main() -> None:
    parser = argparse.ArgumentParser(description="Propose profile updates for one child.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--child", type=int, help="the child's profile item_id (not app_item_id)")
    source.add_argument("--from-saved", type=Path, help="upload a run saved with --save; no model calls")
    parser.add_argument("--save", action="store_true", help="save proposals under runs/ (mock data only)")
    parser.add_argument("--write", action="store_true", help="create proposal items in Podio")
    args = parser.parse_args()

    load_env_file(ENV_PATH)
    client = PodioClient.from_env() if (args.child or args.write) else None

    # Check the proposals app before the model runs, so a missing field fails in seconds.
    proposals_app = None
    if args.write:
        proposals_app = load_proposals_app(client, _require_int_env("PODIO_PROPOSALS_APP_ID"))

    if args.from_saved:
        save_path = args.from_saved
        saved = load_saved(save_path)
    else:
        proposals = propose(client, args.child)
        saved = [{"proposal": p, "item_id": None} for p in proposals]
        save_path = None
        if args.save:
            save_path = RUNS_DIR / f"{args.child}-{datetime.now():%Y%m%d-%H%M%S}.json"
            write_saved(save_path, saved)
            print(f"saved {len(saved)} proposals to {save_path}")

    if not args.write:
        print("dry run: nothing written to Podio (use --write)")
        return

    run_date = date.today()
    created = skipped = 0
    for entry in saved:
        if entry["item_id"] is not None:
            skipped += 1
            continue
        entry["item_id"] = create_proposal(client, proposals_app, entry["proposal"], run_date)
        created += 1
        print(f"created proposal item {entry['item_id']}")
        if save_path:
            write_saved(save_path, saved)

    print(f"created {created} proposal items, skipped {skipped} already created")
    if client.rate_limit_remaining is not None:
        print(f"Podio API calls left this hour: {client.rate_limit_remaining}")


def propose(client: PodioClient, child_id: int) -> list[Proposal]:
    reports = fetch_reports_for_child(client, _require_int_env("PODIO_REPORT_APP_ID"), child_id)
    print(f"child {child_id}: {len(reports)} reports")

    extractions, counts = extract(reports)
    print(
        f"model calls: {counts['calls']}, failed: {counts['llm_errors']}, "
        f"unparseable: {counts['unparseable']}, quotes rejected: {counts['rejected']}, "
        f"extractions kept: {len(extractions)}"
    )

    proposals = group(extractions, reports, model_version=MODEL_NAME, prompt_version=PROMPT_VERSION)
    # No current profile values in the sandbox yet (read.py docstring).
    proposals = drop_current(proposals, child_id, {})
    for p in proposals:
        print(f"proposal: {p.field.value}, {p.evidence_count} of {p.sessions_considered} sessions")
    return proposals


def extract(reports: list[Report]) -> tuple[list[Extraction], dict[str, int]]:
    """
    One model call per (report, field) (spec §13). A failed or unparseable
    call is counted and skipped, not fatal: one bad reply shouldn't cost the
    rest of an hour-long run.
    """
    counts = {"calls": 0, "llm_errors": 0, "unparseable": 0, "rejected": 0}
    kept: list[Extraction] = []
    for i, report in enumerate(reports, 1):
        print(f"report {i}/{len(reports)}: item {report.item_id}")
        for field in TargetField:
            messages = build_prompt(report, field)
            counts["calls"] += 1
            try:
                raws = parse_with_retry(lambda: complete(messages), field)
            except LLMError:
                counts["llm_errors"] += 1
                continue
            except ParseError:
                counts["unparseable"] += 1
                continue
            for raw in raws:
                verified = verify_quote(raw, report)
                if verified is None:
                    counts["rejected"] += 1
                else:
                    kept.append(verified)
    return kept, counts


# --- saved runs (sandbox only, see module docstring) -----------------------


def write_saved(path: Path, saved: list[dict]) -> None:
    path.parent.mkdir(exist_ok=True)
    data = [{"item_id": e["item_id"], "proposal": _proposal_to_dict(e["proposal"])} for e in saved]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_saved(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [{"item_id": e["item_id"], "proposal": _proposal_from_dict(e["proposal"])} for e in data]


def _proposal_to_dict(p: Proposal) -> dict:
    return {
        "child_id": p.child_id,
        "field": p.field.value,
        "proposed_value": p.proposed_value,
        "evidence": [
            {"report_id": e.report_id, "session_date": e.session_date.isoformat(), "quote": e.quote}
            for e in p.evidence
        ],
        "sessions_considered": p.sessions_considered,
        "model_version": p.model_version,
        "prompt_version": p.prompt_version,
    }


def _proposal_from_dict(d: dict) -> Proposal:
    return Proposal(
        child_id=d["child_id"],
        field=TargetField(d["field"]),
        proposed_value=d["proposed_value"],
        evidence=tuple(
            Evidence(report_id=e["report_id"], session_date=date.fromisoformat(e["session_date"]), quote=e["quote"])
            for e in d["evidence"]
        ),
        sessions_considered=d["sessions_considered"],
        model_version=d["model_version"],
        prompt_version=d["prompt_version"],
    )


def _require_int_env(name: str) -> int:
    value = os.environ.get(name, "")
    if not value.isdigit():
        raise SystemExit(f"Set {name} in .env to a numeric id.")
    return int(value)


if __name__ == "__main__":
    main()
