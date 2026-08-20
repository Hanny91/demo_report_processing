# YW_reports — Demo Build Spec

**Status:** stage 0 complete (OAuth spike + seed data); architecture pivoted 2026-08-20 to Podio-native review — see [Progress log](#progress-log) at the end
**Goal:** a demonstrable prototype on mock data, used to support funding applications
**Explicitly out of scope:** real data, production deployment, DPIA, hardware purchase, a local review UI (superseded — see §4)

---

## 1. What this is

Youth workers in an outdoor education alternative provision setting write a daily report per young person per session. These live in Podio, along with a linked "child profile" item made up of structured fields.

The reports contain a lot of useful observation that is effectively write-only — nobody has time to read back through a term's worth. This tool reads the reports and proposes updates to the structured profile fields, with every proposal traceable to the exact sentence it came from. A worker reviews and decides.

**Podio remains the system of record — and now also the review surface.** The local machine hosts no database, no review UI, and no persisted content of any kind. It runs a stateless script that reads reports from Podio, extracts candidate profile updates with a local LLM, and writes proposals into a new Podio app (`Suggested Profile Updates`). Instructors review and action proposals inside Podio itself, under their own login. Full rationale in [§4 Architecture](#4-architecture) below — this was a deliberate pivot away from an earlier local-web-app design.

## 2. Non-negotiable constraints

These are design constraints, not preferences. They exist because the production version handles safeguarding records about vulnerable young people.

1. **All processing local.** No report text is sent to any external API. The language model runs on the machine. Embeddings, if used at all, run locally.
2. **Never auto-write.** The model only ever proposes, into the `Suggested Profile Updates` app. A human accepts, edits or rejects *inside Podio*, under their own login — that action, not the script's write, is what Podio's revision history attributes to them. The script itself writes as the coordinator running the batch, and only ever to the new app, never to `Daily Report` or `Child Profile` directly.
3. **Every proposal cites its source.** A proposal with no verifiable source sentence is discarded, not shown. This is enforced structurally: `source-reports` is a non-null relationship field, and (during extraction) the source reference is a non-null foreign key before it's ever aggregated into a proposal.
4. **No permission escalation.** A worker must never see, via this tool, anything they could not see in Podio directly. Review happening inside Podio itself makes this constraint largely self-enforcing rather than something the app must separately guarantee.

Constraints 3 and 4 can be relaxed for the demo only in the sense that mock data carries no real risk — but the mechanisms must be built and demonstrable, because they are the point.

## 3. Data volumes

Small, and this shapes the architecture:

- ~45 sessions per young person per year
- ~8–10 young people in scope
- so ~400 reports/year, a few thousand over the life of the system

**Consequences:** no vector database, no approximate nearest neighbour index, no incremental sync logic. SQLite throughout. A full rebuild from Podio takes minutes, so rebuild rather than reconcile.

## 4. Architecture

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
  │  not one item per extracted sentence (see decision doc §"Aggregation")
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

## 5. Data model (sketch)

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
- `proposed-value` — text (multi-line)
- `source-reports` — relationship → Daily Report app, **multiple**, required — every report this proposal is aggregated from
- `evidence-count` — number — e.g. "9 of 45 sessions"
- `status` — category, single-select — `proposed` (default) / `accepted` / `edited` / `rejected`
- `reviewed-by` — contact
- `reviewed-at` — date
- `model-version` — text

`status` and `reviewed-by`, changed by the instructor inside Podio, are the evidence of
human-in-the-loop — there's no separate `reviews` table to hold that. The delta between
`proposed-value` and the instructor's edited value (visible via Podio's own item revision
history) is the signal for improving prompts.

### Review workflow (approve/reject) inside Podio

No custom UI is built for this — the review surface is the standard Podio item view plus
one filtered app view. Concretely:

- **Queue:** a saved Podio view on `Suggested Profile Updates`, filtered to `status =
  proposed`, grouped by `child`. This is the instructor's worklist — opening the app to
  this view is the entire "inbox."
- **Reviewing one item:** the instructor opens a proposal, reads `proposed-value` beside
  `source-reports` (Podio renders the linked report items inline, so the source text is
  one click away), and:
  - **Accept as-is** → change `status` to `accepted`, set `reviewed-by` (self) and
    `reviewed-at` (today). No edit to `proposed-value`.
  - **Accept with changes** → edit `proposed-value` directly, then set `status` to
    `edited`, `reviewed-by`, `reviewed-at`. The original model output is still visible in
    Podio's own field-level revision history, so the accepted-vs-proposed delta is never
    lost.
  - **Reject** → change `status` to `rejected`, set `reviewed-by`, `reviewed-at`. No
    profile write follows.
- **No approve/reject buttons are needed or built** — a category field change *is* the
  approval action, and it's what Podio timestamps and attributes under the instructor's
  own login. This is the same mechanism the top-level design already relies on for
  attribution; the review workflow doesn't add anything new, it just names the states.

### Applying an accepted proposal to the profile

Deliberately **not automated for the demo** — this is the one place a wrong write would
land on the actual `Child Profile` app, so it stays a manual, explicit, human action:

- Coordinator (or the reviewing instructor) opens the `accepted`/`edited` proposal
  alongside the child's profile item and copies `proposed-value` into the real field by
  hand.
- Optionally mark the proposal `status` as done via a value like `applied` later if that
  distinction turns out to matter — not added to the closed list above unless a real need
  shows up, to avoid inventing states nobody asked for.
- **Later automation option (not built for demo):** a Podio Workflow Automation
  ("Workflows" in the Podio UI) triggered on `status → accepted/edited` that writes
  `proposed-value` into the matching `Child Profile` field via the relationship in
  `child`. Worth revisiting once the field mapping (`target-field` value → actual Child
  Profile field) is stable and trusted — premature to build against four fields' worth of
  demo data.

## 6. Podio integration notes

- Auth: OAuth as the coordinator running the batch (coordinators have full Podio access already) for the write path; instructors act under their own login for review.
- Relationship fields are type `app`. Write an item ID; read back a nested object containing `item_id` and `title`. This asymmetry breaks naive parsing.
- The relationship field lives on the **report**, pointing at the profile. Retrieve a child's reports via `GET /item/{item_id}/reference/`. Do not put a multi-reference field on the profile — that would mean editing the profile item on every session.
- Use `external_id` rather than numeric `field_id` when reading and writing values.
- Relationships use the global `item_id`, not the `app_item_id` shown in the interface.
- Client libraries are not vendor-maintained. Expect to patch, or call the REST API directly.

## 7. Build stages

| Stage | Deliverable | Notes |
|---|---|---|
| 0 | ✅ OAuth spike — authenticate, pull one report and its linked profile | Done — `podio_oauth_spike.py`. See progress log. |
| 1 | Sync/read Podio reports + profiles into transient in-memory objects for one run | No persistence — replaces the old "Sync Podio → SQLite" stage |
| 2 | Per-report extraction with quote verification | Local model, swappable by config |
| 3 | Aggregation per (child, target-field) across a child's report history | |
| 4 | Create `Suggested Profile Updates` app in Podio (schema in §5); write aggregated proposals | Additive only — never touches Daily Report or Child Profile apps |
| 5 | Instructor review happens natively in Podio | No local review screen to build — this is the point of the pivot |

Stages 2–4 are the demo. Stage 5 requires no build at all, which is itself the thing being
demonstrated. Superseded stages ("browse reports in a local web UI", "review screen")
have been dropped along with the local app.

## 8. Sandbox

Free Podio tier: 100 items per org, 5 employees. Structure created by hand in the UI — currently one profile and one report.

Adequate for the whole integration, including two test users in separate workspaces to verify permission filtering actually works. Not adequate for evaluating extraction quality — for that, generate a larger synthetic corpus and test against SQLite directly.

## 9. Model policy

Build against the weakest model that could plausibly be deployed. Extraction logic that works against a frontier model can fail entirely on an 8B, and discovering that after the fact would invalidate the demo.

Keep the model swappable by configuration so the difference between model sizes can be measured — that turns the hardware line in a funding application into an evidence-based ask rather than a guess.

## 10. Open questions

- Which profile fields would most benefit from being kept current? (ask coordinators)
- Is per-child really the dominant query shape, or do coordinators mostly ask cross-cohort questions?
- Does Upshot, Plinth, or another existing platform already do this? Verify before building further.

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
all, superseding the 2026-08-17 in-memory-SQLite-only decision. Sections 4–7 above have
been rewritten accordingly; the old "Sync Podio → SQLite" / "local web UI" / "review
screen" stages are dropped. (This was originally written up as a separate decision doc,
`podio-review-app-decision.md`, since folded entirely into this spec.)

### Next: stage 1 — read Podio reports + profiles into transient in-memory objects for one run (no persistence).
