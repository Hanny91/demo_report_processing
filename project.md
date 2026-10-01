# YW_reports — Project design

**Status:** proof of concept. Built to work end to end on mock data, within
the MVP scope described below. Next steps are in [further-steps.md](further-steps.md).

**Out of scope:** real data, production deployment, a local review UI, grammar or spelling
assistance, and any post-processing of what an instructor writes (§1).

Section numbers are stable: the code cites them as "spec §N".

---

## 1. What this is

Youth workers in an outdoor education setting write a daily report per young person per
session. The reports live in Podio, alongside a linked profile item for each young person
made up of structured fields.

This tool reads the reports and proposes updates to the
profile fields, each proposal traceable to the exact sentence it came from. A worker reviews
and decides.

**Podio remains the system of record and the review surface.** The local machine hosts no
database, no review UI and no stored content. It runs a stateless script that reads reports
from Podio, extracts candidate updates with a local LLM, and writes proposals into a separate
Podio app, `Suggested Profile Updates`. Instructors review them inside Podio, under their own
login (§5).

### Product framing: reduce the writing burden

The value is **removing the blank page, not analysis**. The tool drafts; the instructor
edits. Editing a prompt is far more manageable than composing from nothing, and it keeps the
entry the instructor's own.

**Design target: drafts that are easy to change, not easy to accept.**

- Keep drafts short.
- Show the supporting evidence next to the edit field.
- Never post-process or "improve" what the instructor writes. Their edit is final.

### Where the value is highest

New arrivals, patchy attendance, and young people seen by rotating instructors. The demo
test: does the output tell an instructor something they didn't already know? A "no" for a
well-known young person is an acceptable result, not a defect.

## 2. Non-negotiable constraints

The production version would handle safeguarding records about vulnerable young people.
These constraints follow from that.

1. **All processing local.** No report text is sent to any external API. The model runs on
   the machine, and so would embeddings if any were used.
2. **Never auto-write.** The model only proposes, into `Suggested Profile Updates`. A human
   accepts, edits or rejects inside Podio, under their own login, so Podio's revision history
   attributes the decision to them. The script writes as the coordinator running the batch,
   and only ever to the proposals app, never to the profile.
3. **Every proposal cites its source.** An extraction whose quote can't be found in its report
   is discarded, not shown (§5, quote verification). A proposal can't be built without at
   least one source report.
4. **No permission escalation.** A worker must never see, through this tool, anything they
   couldn't see in Podio directly. Review inside Podio makes this largely self-enforcing. The
   exception is `evidence-quotes` (§6), which copies report text, so Podio's permissions on
   the source report don't follow it. That is acceptable only where every worker can already
   see every daily report. If a deployment restricts daily reports per user, `evidence-quotes`
   must be revisited. Incident records and formal risk records are never sources or targets.

With mock data, none of this carries real risk. The mechanisms are built anyway, because they
are the point of the demo.

## 3. Automation bias is the main quality risk

The better a draft reads, the less it is scrutinised. Because the tool exists to save effort,
fluent drafts will be accepted unchanged, which turns constraint 2's human review into a
rubber stamp. This is what would make the tool harmful
in use.

Mitigations:

- **Short drafts** (§1).
- **Lead with aggregate evidence** ("appeared in 9 of 15 sessions"), not a block of quoted
  text. It is faster to evaluate and harder to skim past. That is what `evidence-count` (§6)
  is for, and the reviewer should read it before `proposed-value`.
- **Prefer category fields over free text** as targets wherever the profile allows. Proposed
  values can then be checked against the allowed options, and out-of-vocabulary values
  rejected before a human sees them.
- **Measure whether review is actually happening**


## 5. Architecture

```
Podio (system of record and review surface)
  │  read, signed in as the coordinator running the batch
  ▼
Stateless script: Python objects for one run, nothing stored, nothing cached
  │
  ├─ extraction: one model call per (report, target field)
  ├─ quote verification: discard any extraction whose quote isn't in its report
  ├─ aggregation: per (child, target field), across the child's report history
  ▼
Podio: writes proposals into "Suggested Profile Updates" only
  │
  ▼
Instructor accepts, edits or rejects inside Podio, under their own login
  │
  ▼
Applying an accepted proposal to the profile is a separate, manual step
```

**No local persistence.** No database file, no cache between runs. Re-read Podio each time,
or track processed report IDs *in Podio* if recomputation ever matters. Never log field
values or report text, only IDs.

Two exceptions, both development-only and opt-in:

- The eval harness's response cache (§13): mock data only, off by default, not reachable from
  `run.py`.
- `run.py --save`: writes a run's proposals, including quotes, to `runs/` (gitignored), so a
  long extraction isn't lost to a failed upload. Sandbox only. Remove it before anything
  touches real data.

Runs are per child, ahead of a review meeting, a supervision or short-notice cover, not an
organisation-wide sweep on a schedule.

### Extraction first, not retrieval


- Each (report, target field) pair gets exactly one model call: one field per call (§13).
- Each extraction comes from exactly one report, so provenance is structural, not
  reconstructed.


### Quote verification

**No match, no
extraction.** This is the main defence against invented observations, and it is cheap.

The match forgives typography only: dash variants, curly vs straight quotes, "…" vs "...",
and runs of whitespace. Case, punctuation and every word must match. The kept quote is always sliced from the report itself, so what the reviewer sees
in `evidence-quotes` is exactly what the instructor wrote.

A verified quote proves the sentence exists, not that it supports the field. Relevance is
left to the reviewer, and a vocabulary built over time.

### Aggregation, not per-sentence proposals

Extract per report, then aggregate across the child's reports before writing anything: group
by target field, and propose only where the result differs from what's already on the
profile. One proposal per (child, target field), citing every supporting report.

In the MVP, the draft is built from the extracted labels, without a model: labels that differ
only in case, spacing or trailing punctuation count as one phrase, phrases are ranked by
supporting sessions and then recency, and the top three are joined. Every phrase is
therefore a label the model extracted from a verified quote. This groups only near-identical
labels; see [further-steps.md](further-steps.md) for what the first run showed and the
proposed improvements.

**Each run creates new items; it never overwrites earlier ones.** The review queue stays clean by filtering
on `status = proposed`, not by having fewer items.


### Batch run safety rules

1. **Never overwrite an item whose `status` isn't `proposed`.** The script only ever creates
   items, so this holds by construction.
2. **Never re-propose a previously rejected value.** Repeating a rejected suggestion every
   term is the fastest way to make reviewers stop reading. Rejected values would be read back
   from the proposals app at the start of each run. **Not built in the MVP**: it needs one
   more Podio query per run.

## 6. Data model

There is no local data model. The only stored state is the Podio app `Suggested Profile
Updates`. It is additive: no existing report or profile app is modified.

| Field | Type | Written by | Notes |
|---|---|---|---|
| `child` | relationship → profile app | script | |
| `target-field` | category, single | script | `presentation`, `boundaries`, `triggers`, `projects-and-activities` |
| `evidence-count` | number | script | Supporting sessions. **Read first** (§3). |
| `proposed-value` | text, multi-line | script | Short. Never edited by anyone. Podio uses it as the item title. |
| `final-value` | text, multi-line | reviewer | The edited text, if changed. Blank means "same as proposed". |
| `evidence-quotes` | text, multi-line | script | One verified quote per line, dated: `2026-08-07 — "…"`. Never edited. |
| `source-reports` | relationship → reports app, multiple | script | Every report the proposal draws on. |
| `status` | category, single | script, then reviewer | `proposed` / `accepted` / `edited` / `rejected`. Always set to `proposed` explicitly: there is no default. |
| `reviewed-by` | contact | reviewer | |
| `reviewed-at` | date | reviewer | |
| `model-version` | text | script | |
| `prompt-version` | text | script | Prompts change more often than models; both are needed to explain a change in accuracy. |

`podio_api_work/setup_podio_app.py` creates the app with these fields, in this order.

**Target field scope.** `boundaries` and `triggers` here mean descriptive handover guidance
from routine sessions ("gets overwhelmed by loud groups, needs a warning before
transitions"), not a formal risk assessment. Anything that *is* an incident record, a
safeguarding log or a formal risk decision is never a source or a target.

The difference between `proposed-value` and `final-value`, with `status`, is the signal for
improving prompts. Over time it is also the only dataset that could make fine-tuning viable
(§12).

**MVP gaps:** the planned `title` field ("<child> — <field> — <date>") isn't built, so the
"of m sessions" denominator isn't shown anywhere; `evidence-count` holds only the numerator.

### Review workflow inside Podio

No custom UI. The review surface is Podio's own item view plus one saved view:

- **Queue:** a view filtered to `status = proposed`, grouped by `child`. Full history sits
  underneath; the filter keeps the queue clean, never deletion.
- **Reviewing:** read `evidence-count`, then `proposed-value` with `evidence-quotes` beside
  it. `source-reports` links each whole report. Then:
  - **Accept:** set `status` to `accepted`, plus `reviewed-by` and `reviewed-at`.
    `final-value` stays blank.
  - **Edit:** write the edited text into `final-value` (never into `proposed-value`), and set
    `status` to `edited`.
  - **Reject:** set `status` to `rejected`. Rejected proposals are never deleted: they are the
    denominator of any accuracy claim.
- A category field change *is* the approval action, timestamped and attributed by Podio.

### Review telemetry

`review-duration` would record time spent on a proposal. A high accept-unchanged rate is
ambiguous: good drafts, or nobody reading. Time on item tells the two apart.

**It must not be visible to instructors or used as an individual performance measure.** It
exists to validate the system, used in aggregate. Surfacing it invites gaming, which would
poison the data it protects. How to capture it in a Podio-native review is open (§11).

### Applying an accepted proposal

Deliberately manual: this is the one place a wrong write would land on the real profile.
Someone copies `final-value` (or `proposed-value` if no edit was made) into the profile field
by hand. A later option is a Podio workflow triggered on `status → accepted/edited`, once the
mapping from target field to profile field is stable and trusted.

## 7. Podio integration notes

- **Auth:** OAuth (authorization code flow) as the coordinator running the batch. Instructors
  review under their own login.
- **Use `external_id`,** not numeric `field_id`, for reading and writing values.
- **Use the global `item_id`,** not the `app_item_id` shown in the interface. Both are accepted
  in the wrong place without complaint until they silently do the wrong thing.
- **Relationship fields are type `app`.** Write a list of item IDs; read back a nested object
  embedding the whole linked item, its app and its space. Parse only what's needed.
- **Field value shapes depend on type:** text is `{"value": "..."}`, dates are
  `{"start_date": "...", ...}` with no `value` key, relationships are
  `{"value": {"item_id": ..., ...}}`.
- **One call reads a child's reports:** `POST /item/app/{app_id}/filter/` with
  `{"filters": {"<relationship external_id>": [child_item_id]}}` filters on the relationship,
  and the response includes every item's full fields. The relationship lives on the report,
  pointing at the profile; don't add a multi-reference field to the profile.
- **Client libraries aren't vendor-maintained.** This project calls the REST API directly.

### Platform limits

Item volume is not a constraint on paid plans, so the design never collapses or deletes
proposals to save items. What does bind:

- **API calls per hour.** Documented as 1,000 on lower tiers; the response header on the test
  account suggested 250. Check before relying on either. A run makes about 8 calls.
- **250 items per relationship field**, so at most 250 source reports per proposal.
- 100 fields per app, 200 options per category field.

## 9. Test environment

Developed against a free Podio sandbox with mock data: synthetic reports for one fictional
young person, created by `podio_api_work/seed_reports.py`.

**Not verified:** permission filtering with a second user. Review inside Podio relies on Podio's own permissions, but this should be checked on a
paid account before treating the review workflow as complete.

The sandbox is too small to judge extraction quality. That's what the offline eval harness
(§13) is for.

## 10. Model policy

Keep the model swappable by configuration (`MODEL_NAME` in `yw/config.py`), so model sizes
can be compared in the eval harness and hardware needs rest on evidence rather than guesses.
Every candidate is a local model (§2).


## 13. Code architecture

**Exactly one function calls a model** (`llm.complete`). Everything else is plain data
transformation, testable with no network, no GPU and no Podio. **No persistence** (§5): the
process holds Python objects for one run and exits.

```
yw/
  config.py         model name, base_url, temperature, prompt_version
  models.py         dataclasses: Report, RawExtraction, Extraction, Evidence, Proposal
  podio/
    client.py       OAuth and one request wrapper
    read.py         fetch_reports_for_child() and the field readers
    write.py        load_proposals_app() checks the app; create_proposal()
  extract/
    prompt.py       build_prompt(report, field) -> messages
    llm.py          complete(messages) -> str   <- the only model call; check_ready()
    parse.py        tolerant parsing of the reply into RawExtractions
    verify.py       verify_quote(raw, report) -> Extraction | None
  aggregate.py      group() -> one Proposal per (child, field); drop_current()
  run.py            orchestration; the only module with side effects
eval/
  cases.yaml        mock reports with expected extractions
  score.py
```

A separate `schema.py` for Podio field shapes was planned. With one reader, the field readers
live in `read.py` and the external IDs in `write.py`. Split them out once a second module
needs them.

### Step sequence (`run.py`)

1. `read.fetch_reports_for_child(child_id)`. Every report is checked to link to that child,
   so an ignored filter can't mix children.
2. For each report and each target field: `build_prompt` → `complete` → `parse_with_retry` →
   `verify_quote`, discarding failures.
3. `aggregate.group(extractions, reports)` → one `Proposal` per (child, field).
4. `aggregate.drop_current()`: drop proposals matching the current profile value. (The
   rejected-value check is not built; §5.)
5. `write.create_proposal()` for each, with `status: proposed`, only with `--write`.

`--write` checks the proposals app has every field and option it writes *before* the model
runs. `llm.check_ready()` checks Ollama has the model before the first call. Three model
failures in a row stop extraction; finished reports are kept. `--max-reports N` limits a run
for quick checks. `--save` and `--from-saved` are the sandbox-only save described in §5.

### `verify.py` is the choke point

No exact match of the quote in the report means the extraction is discarded. Not flagged, not
down-weighted: discarded. This is the guarantee the provenance design rests on (§2 constraint
3, §5). It also yields the character offsets stored with each extraction.

### `llm.py`

One signature: messages in, string out. It talks to Ollama's OpenAI-compatible endpoint on
`localhost`, so switching models is a config change, never a refactor. Local models only, in
`run.py` and the eval harness alike.

### Eval harness

`eval/cases.yaml` holds mock reports with hand-written expected output: report text, target
field, expected source sentences. It includes **cases where the right answer is nothing**,
because small models over-extract and those cases expose it.

`eval/score.py` reports counts, not pass/fail: expected extractions found and missed,
invented extractions, quote verification failures, unparseable replies. **Invented
extractions weigh most:** a missed observation is a small loss; an invented one reaching a
profile is the failure that matters.

Write cases before tuning prompts, or they will encode what the current prompt already
passes. Every surprise in testing becomes a new case.

The development-only response cache (`--cache`) stores raw replies keyed by (report, field,
prompt version, model) so re-runs are instant: mock data only, off by default, and not
available from `run.py`.

### Deferred: `vocabulary.py`

Curated phrasings per category option, with relevant examples fed into the prompt. It needs
accepted review decisions to exist first. Rejections
should be mined as negative examples too, or the vocabulary entrenches early mistakes.

---

## Appendix: findings from the build

Things the documentation got wrong or left out, found by testing against the live services.

**Podio**

1. The OAuth token exchange (`POST /oauth/token/v2`) takes a **JSON body**, not form-encoded.
   Podio's own pages disagree; form-encoded fails with `"Invalid value null (null): must be
   object"`.
2. Podio's API-key form rejects `localhost` as a domain. `localtest.me` resolves to 127.0.0.1
   and passes. Only the redirect URI's host has to match; port and path are free.
3. Creating an app via the API needs an `icon` in its config (`"<id>.png"`), undocumented.
4. A relationship field's `referenced_apps` takes `[{"app_id": ...}]`, not bare integers.
5. **Don't set a category field's default value through the API.** It either fails on
   creation or, set afterwards, returns 200 while silently corrupting the field (every option
   marked deleted). The script sets `status: proposed` explicitly on every write instead.
6. Writing a field the app doesn't have fails with a bare `400 invalid_value`.
7. Category fields accept option IDs. `write.py` looks them up by option text from
   `GET /app/{app_id}`, so recreating the app doesn't break anything.
8. Multi-line text fields accept HTML. `evidence-quotes` is one escaped `<p>` per quote.

**Ollama (on a laptop with a 2 GB NVIDIA GPU)**

9. Ollama offloads some layers to a small GPU and the model process crashes on real prompts
   (`exit status 0xc0000005`), with both 8B and 3B models. A tiny prompt still works, which is
   misleading. `CUDA_VISIBLE_DEVICES=-1` didn't help; Ollama overrides it. The fix is a
   CPU-only copy of the model: `Modelfile-cpu` with `PARAMETER num_gpu 0`, built with
   `ollama create`.
10. CPU-only on that laptop, `llama3.1:8b` averaged about 41 seconds per call (9–99 s,
    longer when it extracts more), about 37 minutes for 12 reports. One call hit the
    5-minute timeout.
