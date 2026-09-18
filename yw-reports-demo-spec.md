# YW_reports — Demo Build Spec

**Status:** stage 0 complete (OAuth spike + seed data); architecture pivoted 2026-08-20 to Podio-native review; `Suggested Profile Updates` app (app_id `30822495`) provisioned 2026-08-20; decisions addendum folded in 2026-09-18 — see [Progress log](#progress-log) at the end
**Goal:** a demonstrable prototype on mock data, used to support funding applications
**Explicitly out of scope:** real data, production deployment, DPIA, hardware purchase, a local review UI (superseded — see §5), grammar/spelling assistance, and any post-processing of what an instructor writes (see §1)

---

## 1. What this is

Youth workers in an outdoor education alternative provision setting write a daily report per young person per session. These live in Podio, along with a linked "child profile" item made up of structured fields.

The reports contain a lot of useful observation that is effectively write-only — nobody has time to read back through a term's worth. This tool reads the reports and proposes updates to the structured profile fields, with every proposal traceable to the exact sentence it came from. A worker reviews and decides.

**Podio remains the system of record — and now also the review surface.** The local machine hosts no database, no review UI, and no persisted content of any kind. It runs a stateless script that reads reports from Podio, extracts candidate profile updates with a local LLM, and writes proposals into a new Podio app (`Suggested Profile Updates`). Instructors review and action proposals inside Podio itself, under their own login. Full rationale in [§5 Architecture](#5-architecture) below — this was a deliberate pivot away from an earlier local-web-app design.

### Product framing: reduce writing burden

The primary value of profile suggestions is **removing the blank page, not analysis**.
Instructors already hold the knowledge; the cost is writing it up on top of the daily
reports themselves. The tool drafts, the instructor edits.

This does not mean removing writing from the review step. Prompted editing *is* the point
— it is substantially more manageable than composition, and it keeps the entry the
instructor's own.

**Design target: drafts that are easy to change, not easy to accept.**

- Keep drafts short. A long polished paragraph discourages editing; two lines invite it.
- Show the supporting evidence adjacent to the edit field so changes are informed.
- Do not post-process or "improve" what the instructor writes. Their edit is final text.
- No grammar/spelling assistance. Out of scope.

### Where the value is highest

The tool helps most with **new arrivals, patchy attendance, and young people seen by
rotating instructors**. For a long-standing young person with a consistent worker, a human
already holds a better picture than any extraction will produce.

Use this as the demo test: does the output tell an instructor something they did not
already know? A "no" on a well-known young person is an acceptable result, not a defect to
engineer away.

## 2. Non-negotiable constraints

These are design constraints, not preferences. They exist because the production version handles safeguarding records about vulnerable young people.

1. **All processing local.** No report text is sent to any external API. The language model runs on the machine. Embeddings, if used at all, run locally.
2. **Never auto-write.** The model only ever proposes, into the `Suggested Profile Updates` app. A human accepts, edits or rejects *inside Podio*, under their own login — that action, not the script's write, is what Podio's revision history attributes to them. The script itself writes as the coordinator running the batch, and only ever to the new app, never to `Daily Report` or `Child Profile` directly.
3. **Every proposal cites its source.** A proposal with no verifiable source sentence is discarded, not shown. This is enforced structurally: `source-reports` is a non-null relationship field, and (during extraction) the source reference is a non-null foreign key before it's ever aggregated into a proposal.
4. **No permission escalation.** A worker must never see, via this tool, anything they could not see in Podio directly. Review happening inside Podio itself makes this constraint largely self-enforcing rather than something the app must separately guarantee.

Constraints 3 and 4 can be relaxed for the demo only in the sense that mock data carries no real risk — but the mechanisms must be built and demonstrable, because they are the point.

## 3. Automation bias is the main quality risk

The better a draft reads, the less it is scrutinised. Because the product's whole purpose
is to spare effort (§1), fluent drafts will be accepted unchanged — which turns the
human-in-the-loop safeguard of constraint 2 into a rubber stamp. This, not extraction
accuracy on its own, is the thing most likely to make the tool harmful in use.

Mitigations, each of which appears as a concrete requirement elsewhere in this spec:

- **Short drafts.** See §1.
- **Lead each proposal with its aggregate evidence** ("appeared in 9 of 15 sessions")
  rather than a block of quoted text. Faster to evaluate and harder to skim past. That is
  what `evidence-count` (§6) is for, and it is what the reviewer should read before
  `proposed-value`.
- **Prefer category fields over free text as extraction targets** wherever the profile
  structure allows. Proposed values can then be validated against the allowed options, so
  out-of-vocabulary proposals are rejected mechanically before a human ever sees them.
- **Measure whether review is actually happening** rather than assuming it — see
  [Review telemetry](#review-telemetry-time-on-item) in §6.

## 4. Data volumes

Small, and this shapes the architecture:

- ~45 sessions per young person per year
- ~8–10 young people in scope
- so ~400 reports/year, a few thousand over the life of the system

**Consequences:** no vector database, no approximate nearest neighbour index, no incremental sync logic. SQLite throughout. A full rebuild from Podio takes minutes, so rebuild rather than reconcile.

## 5. Architecture

**Superseded (2026-08-20):** the local web app / in-memory SQLite / local review screen
design below the line was the plan through stage 0. It has been replaced by the
Podio-native review design this section now describes. Rationale for the pivot: a local
review UI is one more surface to secure and demo, when Podio already provides auth,
permissions, a UI, and revision-history attribution for free — the local machine doesn't
need to be more than a stateless script.

```
Podio (system of record AND review surface)
  │  read: OAuth as the coordinator running the batch
  ▼
Stateless script — transient Python objects for the duration of one run, nothing
persisted, nothing cached between runs
  │
  ├─ extraction: per-report, one model call each
  ├─ aggregation: per (child, target-field), across that child's full report history —
  │  not one item per extracted sentence (see "Aggregation, not per-sentence proposals")
  ▼
Podio — writes proposals into the new "Suggested Profile Updates" app only
  │
  ▼
Instructor reviews and actions (accept/edit/reject) inside Podio itself, under their
own login — Podio's own revision history attributes the review action to them
  │
  ▼
Applying an accepted proposal to the real profile field is a separate, explicit step
(manual, or a later automation) — not done automatically by this script
```

**No local persistence, full stop** — this supersedes the 2026-08-17 in-memory-SQLite
decision, not just the on-disk question it was weighing. No SQLite file, no in-memory DB
kept alive between runs, nothing cached locally between runs at all: re-run against Podio
each time, or track processed-report IDs *in Podio* (not locally) if avoiding
recomputation matters later. Never log field values or report text — IDs only, if
logging at all.

Batching is per child, ahead of that child's review meeting, not an org-wide sweep on a
schedule — confirm actual review cadence with coordinators before hardcoding a trigger.

### Extraction-first, not retrieval

A year of one child's reports is ~20–40k tokens. There is nothing to retrieve from. Instead of semantic search, map over reports individually:

- Each report is processed once, on import, and the result cached.
- Each extraction is therefore derived from exactly one report — provenance is structural rather than reconstructed.
- Small local models perform far better on one short report than on 45 concatenated ones.
- New report arrives → one call → done. Updating a profile is instant because the work already happened.

Cross-cohort questions ("which young people have shown X this term?") are out of scope for
this script — Podio's own filtering/reporting on `Suggested Profile Updates` is the query
surface now that there's no local cache to run SQL against.

### Quote verification

Models paraphrase when asked to quote. The model returns what it believes is the source sentence; the script then string-matches that back into the report text to compute character offsets. **No exact match means the extraction is rejected**, before it's ever eligible for aggregation. This is the primary defence against fabricated observations and it is cheap.

### Aggregation, not per-sentence proposals

Do not create one proposal per extracted sentence. Extract per-report, then aggregate across a child's reports before writing anything to Podio: group by target field, and only propose where the aggregated value differs from what's already on the profile. One proposal per (child, target-field) combination, citing every supporting report via `source-reports`. This keeps proposal volume low (roughly 5–15 per child per batch, not hundreds) and keeps each proposal well-evidenced rather than one instructor's single phrasing.

**One item per proposal per run, though — not one live item per (child, target-field).** A
later run creates a new item; it does not overwrite the previous one. Collapsing to a
single standing item per field would overwrite history and destroy the accuracy record,
which is the only thing that can substantiate a quality claim later. Podio item volume is
not a reason to collapse (§7), and the reviewer's queue stays clean by filtering on
`status = proposed`, not by having fewer items.

### Voice: organisational, not individual

Multiple instructors write reports for the same young person, so per-instructor voice
matching is not a realistic target and should not be attempted.

Match **organisational register** instead: draw vocabulary from the aggregate of source
reports for that child, keeping the terminology practitioners actually use rather than
smoothing it into generic professional language. Consistency across the organisation is
the goal, not mimicry of whoever happens to be reviewing.

### Batch run safety rules

Two rules the write path must enforce, both about not trampling the review process:

1. **Never overwrite an item whose `status` is not `proposed`.** A batch run must not
   clobber an instructor mid-review, and must not touch anything already accepted, edited
   or rejected.
2. **Never re-propose a previously rejected value.** Repeating the same rejected
   suggestion each term is the fastest way to make reviewers stop reading. Track rejected
   values per (child, target-field) — read them back out of the proposals app at the start
   of a run, since there's no local state to hold them.

## 6. Data model (sketch)

There is no local data model anymore — no `reports`, `profiles`, `extractions`, or
`reviews` tables. The only persisted state is the new Podio app, `Suggested Profile
Updates`, additive only (no existing report or profile app/field is modified):

- `title` — text — set by the script, e.g. "Damian — presentation — 2026-08-20"
- `child` — relationship → Child Profile app
- `target-field` — category, single-select — which profile field this proposes a value for. Closed list: `presentation`, `boundaries`, `triggers`, `projects-and-activities`.

  **Scope rationale (revisited 2026-08-20):** `boundaries` and `triggers` were initially
  flagged against the general rule that safeguarding/risk fields should never be valid
  suggestion targets, since those names read as risk-adjacent. Clarified: as fields on
  this profile, they are descriptive handover guidance for whoever next works with the
  child (e.g. "gets overwhelmed by loud groups, needs a 5-minute warning before
  transitions") extracted from ordinary session reports — not a clinical or formal risk
  assessment. They're in scope. **The exclusion rule still stands** for anything that *is*
  a dedicated incident report, safeguarding log, or formal risk-register field, should
  such a thing exist on this or a future profile app — the distinction is "ordinary
  profile field populated from routine observation" vs. "record of an incident or formal
  risk decision," not the field's name alone.
- `proposed-value` — text (multi-line) — **written by the script and never edited by anyone.** Keep it short (§1): two lines that invite an edit, not a paragraph polished enough to wave through.
- `final-value` — text (multi-line) — the instructor's text, where they changed something. Blank means "same as proposed". Without this field the proposed-versus-final delta is lost and edit quality can't be measured; Podio's field-level revision history does record the change, but it isn't queryable in aggregate, so it is not a substitute.
- `source-reports` — relationship → Daily Report app, **multiple**, required — every report this proposal is aggregated from. Podio caps this at 250 linked items — a non-issue, see §7.
- `evidence-count` — number — e.g. "9 of 45 sessions". The first thing the reviewer should read (§3).
- `status` — category, single-select — `proposed` / `accepted` / `edited` / `rejected`. **Set explicitly to `proposed` by the script on every item it creates, never left blank** — there is no field default, and trying to add one corrupts the field (progress log, stage 4 provisioning).
- `reviewed-by` — contact
- `reviewed-at` — date
- `review-duration` — number (seconds) — time spent on this proposal. See [Review telemetry](#review-telemetry-time-on-item) below.
- `model-version` — text
- `prompt-version` — text — sits alongside `model-version`. Prompts change far more often than models; without both, a change in accuracy can't be attributed to either.

`status` and `reviewed-by`, changed by the instructor inside Podio, are the evidence of
human-in-the-loop — there's no separate `reviews` table to hold that. The delta between
`proposed-value` and `final-value` is the signal for improving prompts, and accumulated
over time it is also the only dataset that could make fine-tuning viable if that is ever
wanted (§12). Structure it properly now; decide about it later.

### Review workflow (approve/reject) inside Podio

No custom UI is built for this — the review surface is the standard Podio item view plus
one filtered app view. Concretely:

- **Queue:** a saved Podio view on `Suggested Profile Updates`, filtered to `status =
  proposed`, grouped by `child`. This is the instructor's worklist — opening the app to
  this view is the entire "inbox." Full proposal history sits underneath it; the filter is
  what keeps the queue clean, never deletion (§7).
- **Reviewing one item:** the instructor opens a proposal, reads `evidence-count` first,
  then `proposed-value` beside `source-reports` (Podio renders the linked report items
  inline, so the source text is one click away), and:
  - **Accept as-is** → change `status` to `accepted`, set `reviewed-by` (self) and
    `reviewed-at` (today). `final-value` stays blank, which is how "unchanged" is
    recorded. Nothing is copied by hand for the common case.
  - **Accept with changes** → write the edited text into `final-value` — **not** into
    `proposed-value`, which the script owns and nobody edits — then set `status` to
    `edited`, `reviewed-by`, `reviewed-at`. Holding both values in fields rather than in
    revision history is what makes the proposed-versus-final delta measurable across a
    whole batch.
  - **Reject** → change `status` to `rejected`, set `reviewed-by`, `reviewed-at`. No
    profile write follows. **Rejected proposals are never deleted** — they are the
    denominator in any accuracy claim, and the rejected value is what stops the same
    suggestion coming back next term (§5, batch run safety rules).
- **No approve/reject buttons are needed or built** — a category field change *is* the
  approval action, and it's what Podio timestamps and attributes under the instructor's
  own login. This is the same mechanism the top-level design already relies on for
  attribution; the review workflow doesn't add anything new, it just names the states.

### Review telemetry: time-on-item

`review-duration` records the seconds spent on a proposal, alongside the accept/edit/reject
decision in `status`.

Rationale: a high accept-unchanged rate is ambiguous — it may mean the drafts are good, or
it may mean nobody is reading them (§3). Time-on-item disambiguates the two. A batch
accepted instantly is a different signal from one accepted after consideration, and only
the second one tells you anything about extraction quality.

**This measure must not be visible to instructors and must not be presented as an
individual performance metric.** It exists to validate extraction quality, not to monitor
staff. Surfacing it invites gaming, which would poison the very review data it is meant to
protect. Use it in aggregate, for assessing the system.

Capturing it in a Podio-native review is not solved yet — Podio timestamps the revision,
not the reading. The crude proxy available today is the gap between consecutive status
changes by the same reviewer in one sitting; whether that is good enough is an open
question (§11).

### Applying an accepted proposal to the profile

Deliberately **not automated for the demo** — this is the one place a wrong write would
land on the actual `Child Profile` app, so it stays a manual, explicit, human action:

- Coordinator (or the reviewing instructor) opens the `accepted`/`edited` proposal
  alongside the child's profile item and copies `final-value` — or `proposed-value` where
  no edit was made — into the real field by hand.
- Optionally mark the proposal `status` as done via a value like `applied` later if that
  distinction turns out to matter — not added to the closed list above unless a real need
  shows up, to avoid inventing states nobody asked for.
- **Later automation option (not built for demo):** a Podio Workflow Automation
  ("Workflows" in the Podio UI) triggered on `status → accepted/edited` that writes
  `final-value`, falling back to `proposed-value` when blank, into the matching
  `Child Profile` field via the relationship in `child`. Worth revisiting once the field mapping (`target-field` value → actual Child
  Profile field) is stable and trusted — premature to build against four fields' worth of
  demo data.

## 7. Podio integration notes

- Auth: OAuth as the coordinator running the batch (coordinators have full Podio access already) for the write path; instructors act under their own login for review.
- Relationship fields are type `app`. Write an item ID; read back a nested object containing `item_id` and `title`. This asymmetry breaks naive parsing.
- The relationship field lives on the **report**, pointing at the profile. Retrieve a child's reports via `GET /item/{item_id}/reference/`. Do not put a multi-reference field on the profile — that would mean editing the profile item on every session.
- Use `external_id` rather than numeric `field_id` when reading and writing values.
- Relationships use the global `item_id`, not the `app_item_id` shown in the interface.
- Client libraries are not vendor-maintained. Expect to patch, or call the REST API directly.

### Platform limits that actually bind

**Item volume is not one of them.** The 100-item cap is free-tier only; paid plans allow
unlimited items. Do not design around item count — in particular, do not collapse
proposals to one live item per (child, target-field) (§5) and do not delete rejected ones
(§6) in order to save item quota.

What does bind:

- **1,000 API calls/hour** on Free and Plus tiers — the real constraint on batch runs. Use
  filtered bulk queries (`POST /item/app/{app_id}/filter/`) rather than per-item fetches,
  both when reading a child's reports and when reading back previously rejected values for
  the safety rule in §5.
- **250 items per relationship field** — caps `source-reports` at 250 supporting reports.
  Accepted as a non-issue: at ~45 sessions/year (§4) that's 5+ years out, by which point
  staff know the young person well and a generated profile has limited value anyway (§1,
  where the value is highest).
- **2,500 comments per item; 100 fields per app; 200 categories per category field.**
  Nothing in this design is near any of these; recorded so future additions — especially
  the category-field extraction targets in §3 — can be checked against them.

## 8. Build stages

| Stage | Deliverable | Notes |
|---|---|---|
| 0 | ✅ OAuth spike — authenticate, pull one report and its linked profile | Done — `podio_oauth_spike.py`. See progress log. |
| 1 | Sync/read Podio reports + profiles into transient in-memory objects for one run | No persistence — replaces the old "Sync Podio → SQLite" stage |
| 2 | Per-report extraction with quote verification | Local model, swappable by config |
| 3 | Aggregation per (child, target-field) across a child's report history | |
| 4 | Create `Suggested Profile Updates` app in Podio (schema in §6); write aggregated proposals | Additive only — never touches Daily Report or Child Profile apps. Sets `status: proposed` explicitly and obeys the batch run safety rules in §5 |
| 5 | Instructor review happens natively in Podio | No local review screen to build — this is the point of the pivot |

Stages 2–4 are the demo. Stage 5 requires no build at all, which is itself the thing being
demonstrated. Superseded stages ("browse reports in a local web UI", "review screen")
have been dropped along with the local app.

## 9. Sandbox

Free Podio tier: 100 items per org, 5 employees — a *sandbox* limit, not a design one. Paid plans allow unlimited items, so nothing in this design should be shaped by item volume (§7). Structure created by hand in the UI through stage 0; the `Suggested Profile Updates` app (stage 4) is now created by [setup_podio_app.py](podio%20api%20work/setup_podio_app.py) instead — see the progress log.

**Blocked (found 2026-08-20):** user management — adding a second test account to actually verify permission filtering — is not part of the free plan. The originally-planned "two test users in separate workspaces to verify permission filtering actually works" check can't be done in this sandbox without a paid upgrade. Deferred, not resolved — open question, not yet decided whether to pay for a temporary upgrade, skip the check and trust Podio's own permission system (it isn't something this project builds), or defer verification to the real org's paid account later. Revisit before treating the review workflow as demo-complete.

Not adequate for evaluating extraction quality — for that, generate a larger synthetic corpus and test against SQLite directly.

## 10. Model policy

Build against the weakest model that could plausibly be deployed. Extraction logic that works against a frontier model can fail entirely on an 8B, and discovering that after the fact would invalidate the demo.

Keep the model swappable by configuration so the difference between model sizes can be measured — that turns the hardware line in a funding application into an evidence-based ask rather than a guess.

## 11. Open questions

- Which profile fields would most benefit from being kept current? (ask coordinators)
- Is per-child really the dominant query shape, or do coordinators mostly ask cross-cohort questions?
- Does Upshot, Plinth, or another existing platform already do this? Verify before building further.
- Which of the target profile fields could be **category fields** rather than free text? Each one converted turns a quality problem into a validation problem (§3).
- How is `review-duration` (§6) actually captured, given review happens natively in Podio and Podio timestamps revisions rather than reading time? Is the consecutive-status-change proxy good enough, or does this need a thin review helper after all?

## 12. Deferred

Recorded so they are neither re-litigated nor accidentally started:

- **Fine-tuning.** Not now. No training data exists yet, most of the apparent needs are
  prompt problems, and fine-tuning on real reports would bake children's records
  irreversibly into weights. The review data (`proposed-value` / `final-value` / `status`)
  accumulates the dataset that would make it viable later — structure it well now, decide
  later.
- **Evaluation and funding-report tooling.** Sits on the same extraction layer but is
  aimed at managers rather than instructors. Deliberately second: the profile workflow is
  what measures extraction accuracy in the first place, and aggregate statistics should not
  be published to funders on unvalidated extractions.
- **Upstream dictation on the daily reports themselves.** Simpler than this project and
  addresses the writing burden (§1) more directly. Worth raising with coordinators
  separately; not part of this build.

## Progress log

### Stage 0 — done (2026-08-17)

Note: the local-app assumptions stage 0 was built under (a persisted or in-memory local
cache feeding a local review UI) were superseded by the pivot documented below in
[Architecture pivot (2026-08-20)](#architecture-pivot-2026-08-20). Stage 0's OAuth/seed-data
work itself is unaffected and still stands.

Two scripts in this directory, both plain scripts per the constraints (no
framework, no DB, credentials from `.env` — see `.env.example`):

- `podio_oauth_spike.py` — user OAuth (authorization code flow), fetch one
  report item, follow its relationship field to the linked profile item,
  print the raw shape of every field on both. `--list-items` lists items
  in an app to find a real `item_id`.
- `seed_reports.py` — creates synthetic report items via the API so there's
  something to work with in later stages without hand-entering data in the
  Podio UI. Currently 10 mock 1:1 outdoor-session reports for "Damian",
  linked to the one profile item, June–August 2026.

**Sandbox object IDs** (this org/space only — will differ per environment):

| What | Value |
|---|---|
| Space | `hannys-ideas/test-reports`, space_id `10570017` |
| `reports` app | app_id `30816198` — fields: `title` (text, the report body), `date-of-session` (date), `profile-2` (app/relationship → profile) |
| `profile` app | app_id `30816226` — fields: `title` (text, name), `date-of-birth` (date) |
| Damian's profile item | item_id `3352054230` (app_item_id `1` — do not confuse the two, see below) |
| `Suggested Profile Updates` app | app_id `30822495` — created via `setup_podio_app.py`, fields per spec §6. See [Stage 4 provisioning (2026-08-20)](#stage-4-provisioning-2026-08-20) below. |

**Things the docs got wrong or left ambiguous, resolved by testing against
the live API rather than guessing:**

1. **Token exchange (`POST https://api.podio.com/oauth/token/v2`) wants a
   JSON body**, not form-urlencoded. Podio's own docs disagreed with each
   other on this. Confirmed by the actual error when sent form-encoded:
   `"Invalid value null (null): must be object"` — Podio was trying to
   parse the body as JSON and finding nothing.
2. **`app_item_id` and `item_id` are genuinely different numbers and both
   get accepted without complaint in the wrong context** — until they
   silently do the wrong thing. Wrote `"profile-2": [1]` (Damian's
   `app_item_id`) instead of `[3352054230]` (his `item_id`); Podio
   returned a clean `404 Not Found — No item with id 1 could be found`
   rather than linking to the wrong item, which is the good outcome, but
   it's an easy mistake to make silently in code that doesn't check the
   response.
3. **Relationship field read/write asymmetry is worse than expected.**
   Write is just `{"profile-2": [item_id]}`. Read-back embeds the *entire*
   linked item, which itself embeds its *entire* app definition (icon,
   config, workflow), which embeds the *entire* space (org id, quota
   counts, dead ShareFile fields) — around 150 lines of JSON to carry one
   title and one id. Schema/cache design needs to explicitly decide what
   to discard from this, not store it verbatim.
4. **Field value shape depends on `type` and isn't uniformly `{"value":
   ...}`.** Text fields are `{"value": "..."}`; date fields are
   `{"start": "...", "start_date": "...", ...}` with no `"value"` key at
   all; app fields are `{"value": {...full nested item...}}`. Any parser
   has to branch on `type`, not assume one shape.
5. **`refs` on an item is a live back-reference count** Podio maintains
   itself (e.g. the profile item shows `"count": 2` for the reports app) —
   useful as a free integrity check that the local cache isn't missing
   rows, since it's Podio's own count rather than something computed
   locally.
6. **Podio's API-key registration form rejects `localhost` as a domain**
   (it wants a real dotted domain). Workaround: register `localtest.me`,
   which is a real, publicly resolvable domain that DNS-resolves to
   `127.0.0.1` — passes their validation, still hits a local server. The
   domain registered only has to match the *host* of the `redirect_uri`
   used at authorize time, not the full URL — port and path are free.

### Architecture pivot (2026-08-20)

Decided to drop the local web app entirely. Review now happens inside Podio itself, via a
new `Suggested Profile Updates` app, rather than in a local review screen. The local
machine runs a stateless script only (read from Podio → extract → aggregate per
child/field → write proposals to the new app); nothing is persisted or cached locally at
all, superseding the 2026-08-17 in-memory-SQLite-only decision. Sections 5–8 above have
been rewritten accordingly; the old "Sync Podio → SQLite" / "local web UI" / "review
screen" stages are dropped. (This was originally written up as a separate decision doc,
`podio-review-app-decision.md`, since folded entirely into this spec.)

### Stage 4 provisioning (2026-08-20)

Repo moved into its own git repo (`demo_report_processing`) today; paths in this doc are
now relative to that repo root, not the old `podio api work/` folder used in earlier
entries. `podio-review-app-decision.md` didn't survive the move as a separate file — its
content was folded entirely into this spec (see the architecture pivot entry above and
§5–§8) — links to it elsewhere in this doc now point at sections here instead.

Wrote [setup_podio_app.py](podio%20api%20work/setup_podio_app.py) to create the
`Suggested Profile Updates` app (§6 schema) via `POST /app/` instead of clicking it
together by hand — the point being that an admin on the real org's Podio account can run
one command later instead of re-deriving field types and external_ids from this doc.
Created successfully: **app_id `30822495`**, space `10570017`.

**Write-shape gotchas found, same "verify against live API" approach as stage 0:**

1. **`icon` is required in app config**, undocumented until a 400. Format is `"<id>.png"`;
   reused `"3.png"` from the existing `reports` app (confirmed via `GET /app/30816198`)
   since any valid id works and this one is known-good.
2. **Relationship field `referenced_apps` needs `[{"app_id": ...}]`**, not bare integers
   — confirmed by testing a throwaway field on the `reports` app (created, verified the
   shape, then deleted it) before trusting it in the real payload.
3. **Setting a category field's default value via the API is unsafe.** Including
   `default_value` in the field-creation POST either 400s (`"missing required
   properties: ['type']"`) or 500s once a `type` key is added. Worse: setting it
   *after* creation via `PUT /app/{app_id}/field/{field_id}` doesn't error — it silently
   corrupts the field. All four `status` options flipped to `"status": "deleted"` and
   `display` changed from `"list"` to `"inline"`, with a `200` response. Recovered by
   deleting the app and re-running the script clean rather than trying to patch a patch.
   The default-value control also wasn't findable in this space's UI (§9 notes the
   free-plan constraints hit today more generally).
4. **Decided (2026-08-20): stop trying to make `status` default to `proposed` at the
   Podio layer at all.** Instead, the extraction script (stage 4's write path) must
   always set `status: proposed` explicitly on every proposal item it creates. This was
   already the only safe behavior regardless of whether a field default existed —
   relying on a default is a footgun the moment it silently changes, or a future write
   path forgets it's depending on one. `setup_podio_app.py`'s follow-up checklist
   documents this so it isn't re-attempted.

**Also found today: the "two test users" permission check from §9 is blocked** — adding
a second account isn't available on this org's free plan. Deferred, see §9.

### Decisions addendum folded in (2026-09-18)

A set of product and quality decisions was written up separately as an addendum to this
spec and the (already-folded) `podio-review-app-decision.md`. Same treatment as that
decision doc: merged into the body above rather than kept alongside it, so there is one
document to read. Where it conflicted with what was here, it won. What changed:

- **§1** — product framing added: the value is removing the blank page, not analysis, and
  the design target is drafts that are easy to *change*, not easy to accept. Scoping
  principle added: value is highest for less-known young people, and "tells them nothing
  new" is an acceptable demo result for a well-known one.
- **§3 (new)** — automation bias named as the main quality risk, with mitigations: short
  drafts, evidence-first presentation, and a preference for category fields over free text
  as extraction targets. Sections 3–10 renumbered to 4–11 accordingly.
- **§5** — voice target set to organisational register rather than per-instructor voice
  matching (multiple instructors write for the same child, so voice matching was never
  realistic). Batch run safety rules added: never overwrite an item whose status isn't
  `proposed`, never re-propose a previously rejected value. One item per proposal per run
  made explicit — no collapsing to one live item per (child, target-field).
- **§6** — schema additions: `final-value` (instructor edits go here; `proposed-value` is
  script-owned and never edited), `prompt-version`, and `review-duration`. `status` must be
  written explicitly as `proposed` on creation. Review workflow rewritten to match, and
  rejected proposals are never deleted — they are the denominator of any accuracy claim.
  Review telemetry documented, including the rule that it must never be visible to
  instructors or used as an individual performance metric.
- **§7, §9** — Podio item counts dropped as a design constraint (the 100-item cap is
  free-tier only). The limits that do bind are now documented: 1,000 API calls/hour, 250
  items per relationship field, and the comment/field/category caps.
- **§12 (new)** — what is deliberately deferred: fine-tuning, evaluation and
  funding-report tooling, and upstream dictation on the daily reports.

Two new open questions came out of this (§11): which target fields can become category
fields, and how `review-duration` is actually captured in a Podio-native review.

### Next: stage 1 — read Podio reports + profiles into transient in-memory objects for one run (no persistence).
