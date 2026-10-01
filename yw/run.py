"""
One batch run for one child (spec §5, §13 step sequence). The only module
with side effects.

    read      podio/read.fetch_reports_for_child
    extract   per (report, field): build_prompt -> complete -> parse_with_retry -> verify_quote
    aggregate aggregate.group -> aggregate.drop_current
    write     podio/write.create_proposal, only with --write

Usage:
    python -m yw.run --child ID                        dry run: no writes, logs counts
    python -m yw.run --child ID --save                 also save proposals to runs/
    python -m yw.run --child ID --save --write         save, then upload
    python -m yw.run --from-saved runs/FILE --write    upload a saved run, no model calls

Logs go to the console and to runs/<child>-<timestamp>.log, so an unattended
run can be read afterwards. They hold ids, counts, timings and error
messages only, never report text or extracted values (spec §5). LLMError,
ParseError and PodioError messages are built to exclude both.

Before any model call, llm.check_ready confirms Ollama is up and has the
model. Three model failures in a row stop extraction early, rather than
spending hours failing. Reports finished before that are kept. The report in
progress is dropped, so "n of m sessions" only counts fully processed
reports.

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
import logging
import os
import time
from datetime import date, datetime
from pathlib import Path

from yw.aggregate import drop_current, group
from yw.config import MODEL_NAME, PROMPT_VERSION
from yw.extract.llm import LLMError, check_ready, complete
from yw.extract.parse import ParseError, parse_with_retry
from yw.extract.prompt import build_prompt
from yw.extract.verify import verify_quote
from yw.models import Evidence, Extraction, Proposal, Report, TargetField
from yw.podio.client import ENV_PATH, REPO_ROOT, PodioClient, load_env_file
from yw.podio.read import fetch_reports_for_child
from yw.podio.write import create_proposal, load_proposals_app

RUNS_DIR = REPO_ROOT / "runs"

# Stop extraction after this many model failures in a row: the model is
# down, not having a bad moment.
MAX_CONSECUTIVE_FAILURES = 3

log = logging.getLogger("yw.run")


def main() -> None:
    parser = argparse.ArgumentParser(description="Propose profile updates for one child.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--child", type=int, help="the child's profile item_id (not app_item_id)")
    source.add_argument("--from-saved", type=Path, help="upload a run saved with --save; no model calls")
    parser.add_argument("--save", action="store_true", help="save proposals under runs/ (mock data only)")
    parser.add_argument("--write", action="store_true", help="create proposal items in Podio")
    parser.add_argument(
        "--max-reports", type=int, metavar="N",
        help="only use the child's N oldest reports: a quick end-to-end check, not a real run",
    )
    args = parser.parse_args()

    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    run_name = f"{args.child}-{stamp}" if args.child else f"upload-{stamp}"
    log_path = setup_logging(RUNS_DIR / f"{run_name}.log")
    log.info("run started: child=%s save=%s write=%s from_saved=%s", args.child, args.save, args.write, args.from_saved)
    log.info("model=%s prompt_version=%s log=%s", MODEL_NAME, PROMPT_VERSION, log_path)

    try:
        _run(args, run_name)
    except (LLMError, SystemExit) as exc:
        log.error("run stopped: %s", exc)
        raise SystemExit(1) from None
    except Exception:
        log.exception("run failed")
        raise


def _run(args, run_name: str) -> None:
    load_env_file(ENV_PATH)
    client = PodioClient.from_env() if (args.child or args.write) else None

    # Check everything that can fail fast before the long part starts.
    proposals_app = None
    if args.write:
        proposals_app = load_proposals_app(client, _require_int_env("PODIO_PROPOSALS_APP_ID"))
        log.info("proposals app %s checked: all fields and options present", proposals_app.app_id)

    if args.from_saved:
        save_path = args.from_saved
        saved = load_saved(save_path)
        log.info("loaded %d proposals from %s", len(saved), save_path)
    else:
        check_ready()
        log.info("Ollama is up and has %s", MODEL_NAME)
        proposals = propose(client, args.child, args.max_reports)
        saved = [{"proposal": p, "item_id": None} for p in proposals]
        save_path = None
        if args.save:
            save_path = RUNS_DIR / f"{run_name}.json"
            write_saved(save_path, saved)
            log.info("saved %d proposals to %s", len(saved), save_path)

    if not args.write:
        log.info("dry run: nothing written to Podio (use --write)")
        return

    created = skipped = 0
    for entry in saved:
        if entry["item_id"] is not None:
            skipped += 1
            continue
        p = entry["proposal"]
        entry["item_id"] = create_proposal(client, proposals_app, p)
        created += 1
        log.info("created proposal item %s (%s)", entry["item_id"], p.field.value)
        if save_path:
            write_saved(save_path, saved)

    log.info("created %d proposal items, skipped %d already created", created, skipped)
    if client.rate_limit_remaining is not None:
        log.info("Podio API calls left this hour: %s", client.rate_limit_remaining)


def setup_logging(path: Path) -> Path:
    path.parent.mkdir(exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    for handler in (logging.StreamHandler(), logging.FileHandler(path, encoding="utf-8")):
        handler.setFormatter(formatter)
        log.addHandler(handler)
    log.setLevel(logging.INFO)
    return path


def propose(client: PodioClient, child_id: int, max_reports: int | None = None) -> list[Proposal]:
    reports = fetch_reports_for_child(client, _require_int_env("PODIO_REPORT_APP_ID"), child_id)
    if max_reports is not None:
        log.warning("--max-reports %d: using %d of %d reports (test run)", max_reports, min(max_reports, len(reports)), len(reports))
        reports = reports[:max_reports]
    log.info("child %s: %d reports, %d model calls to make", child_id, len(reports), len(reports) * len(TargetField))

    extractions, processed, counts = extract(reports)
    log.info(
        "extraction done: reports processed %d/%d, model calls %d, failed %d, unparseable %d, "
        "quotes rejected %d, extractions kept %d",
        len(processed), len(reports), counts["calls"], counts["llm_errors"], counts["unparseable"],
        counts["rejected"], len(extractions),
    )

    proposals = group(extractions, processed, model_version=MODEL_NAME, prompt_version=PROMPT_VERSION)
    # No current profile values in the sandbox yet (read.py docstring).
    proposals = drop_current(proposals, child_id, {})
    for p in proposals:
        log.info("proposal: %s, %d of %d sessions", p.field.value, p.evidence_count, p.sessions_considered)
    if not proposals:
        log.info("no proposals")
    return proposals


def extract(reports: list[Report]) -> tuple[list[Extraction], list[Report], dict[str, int]]:
    """
    One model call per (report, field) (spec §13). A failed or unparseable
    call is logged, counted and skipped: one bad reply shouldn't cost the
    rest of a long run. MAX_CONSECUTIVE_FAILURES model failures in a row
    stop extraction. Returns the kept extractions, the reports fully
    processed, and the counts.
    """
    counts = {"calls": 0, "llm_errors": 0, "unparseable": 0, "rejected": 0}
    kept: list[Extraction] = []
    processed: list[Report] = []
    consecutive_failures = 0
    started = time.monotonic()

    for i, report in enumerate(reports, 1):
        log.info("report %d/%d: item %s (%s)", i, len(reports), report.item_id, report.session_date)
        report_kept: list[Extraction] = []
        for field in TargetField:
            messages = build_prompt(report, field)
            counts["calls"] += 1
            call_started = time.monotonic()
            try:
                raws = parse_with_retry(lambda: complete(messages), field)
            except LLMError as exc:
                counts["llm_errors"] += 1
                consecutive_failures += 1
                log.warning("  %s: model call failed after %.0fs: %s", field.value, time.monotonic() - call_started, exc)
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    log.error(
                        "%d model failures in a row, stopping extraction; keeping %d fully processed reports",
                        consecutive_failures, len(processed),
                    )
                    return kept, processed, counts
                continue
            except ParseError as exc:
                counts["unparseable"] += 1
                consecutive_failures = 0
                log.warning("  %s: unparseable twice after %.0fs: %s", field.value, time.monotonic() - call_started, exc)
                continue

            consecutive_failures = 0
            verified = [v for v in (verify_quote(raw, report) for raw in raws) if v is not None]
            counts["rejected"] += len(raws) - len(verified)
            report_kept.extend(verified)
            log.info(
                "  %s: %.0fs, %d extracted, %d verified",
                field.value, time.monotonic() - call_started, len(raws), len(verified),
            )

        kept.extend(report_kept)
        processed.append(report)
        elapsed = time.monotonic() - started
        remaining = elapsed / i * (len(reports) - i)
        log.info("report %d/%d done; elapsed %.0f min, about %.0f min left", i, len(reports), elapsed / 60, remaining / 60)

    return kept, processed, counts


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
