# YW_reports — Further steps

The design document, [project.md](project.md), remains the source of truth for the design.
This file collects what to do next and why. "Spec §N" means section N of project.md.

Last updated 2026-10-01, after the first end-to-end run.

---

## 1. Activities as a two-level category field

**Status:** proposal. Needs coordinator input before anything is built.

### Why

The first run showed that aggregation only works where the model's labels repeat word for
word (`yw/aggregate.py`)

### Proposal: area plus detail

- **Area: a category field of about 15–25 options, chosen by the coordinators.** For
  example climbing, fire and cooking, den-building and shelters, navigation, nature and
  wildlife, water, woodwork and tools, art, music, games and sport. Plus an explicit
  **other**.
- **Detail: free text in the report's own words.** For example "made a drum from a
  bucket", "orienteering with map and compass".

What this gives:

- **Exact aggregation by area.** Evidence counts become meaningful, where free-text
  matching failed in the first run.
- **The specifics are kept.** Details are listed under each area for the reviewer, without
  needing to match each other.
- **Mechanical validation.** An area that isn't on the list is rejected before a human sees
  it (spec §3).
- **"Other" shows where the list has gaps.** If "other" keeps coming back with similar
  details ("baking", "pizza oven"), the coordinator adds an area. The list grows from real
  reports rather than guesses. This is the same curation loop as the deferred
  `vocabulary.py` (spec §13), and it should probably share its storage once that exists.

### Questions for the coordinators

1. **What are the areas?** They should come from the coordinators, not from the model or a
   developer. They know how the centre thinks about its own activities, and drafts should
   use the organisation's own terms.
2. **Should the Child Profile field itself become a multi-select category?** That changes
   their existing app. Proposals could use areas either way.

### What changes in code, once the areas are agreed

- `prompt.py`: for `projects-and-activities`, ask for an area from the list plus a detail,
  and bump `PROMPT_VERSION`.
- `parse.py` / `verify.py`: reject an area not on the list, the same way an unverified
  quote is rejected.
- `aggregate.py`: group by area rather than by the free-text label. Details become part of
  the draft.
- `setup_podio_app.py` / `write.py`: depends on question 2.
- `eval/cases.yaml`: add activity cases with expected areas *before* tuning the prompt
  (spec §13).

---

## 2. Test with reports written by instructors

**Status:** next, once instructors can write some.

The mock reports were written to be extractable. Reports written by instructors will vary
more in wording, so expect fewer groupings and more fallback to generic labels (see 3).
Put them in `eval/cases.yaml` with the instructors' own expected answers, written before
any prompt change. Otherwise the cases will measure the prompt rather than echo it (spec
§13).

## 3. Aggregation quality findings from the first run

**Status:** found, not acted on. Measure with 2 before changing anything.

- **Exact-match ranking rewards vague labels.** Specific observations rarely repeat word for
  word; generic labels ("mood", "engaged") repeat constantly, so they win the ranking. This
  pulls drafts away from the organisation's own terms towards generic language.
- **The prompt's own vocabulary leaks into drafts.** The triggers draft came out as
  "dysregulation; withdrawal; triggered distress", words taken from the field guidance in
  `prompt.py` rather than from the reports. Cheap fix to try: ask for labels in the
  report's own words, and stop listing example vocabulary the model can copy.
- **Verified doesn't mean relevant.** The quote check proves a quote exists in the report,
  not that it supports the field. "More confident on the ropes than I expected" appeared as
  evidence under both triggers and boundaries. The reviewer reading the evidence beside the
  draft is the only safeguard, so keep the evidence visible next to the draft.
- **A merging step** for presentation, boundaries and triggers: only worth it once labels
  are good, and it would need a check that every merged phrase traces back to source
  values.

## 4. One proposal item per group, not per field

**Status:** proposal. It changes a spec rule (§5), and only applies once a merging step
exists (see 3).

### The problem

Today each (child, field) gets one proposal item. Its `proposed-value` joins up to three
separate observations, and its evidence mixes the quotes for all of them. The reviewer has
to accept, edit or reject the whole box at once, even when one observation is right and
another is wrong.

A Podio item can't have repeating sections: every item has the same fixed fields. So "one
card with room for several groups" has to be built from separate items.

### Proposal

After merging, **each group becomes its own proposal item**, with the same fields as now:

- `proposed-value`: that group's label, for example "frustrated after repeated failure"
- `evidence-count`: the reports in that group only
- `evidence-quotes`: that group's quotes, dated
- `source-reports`: links to that group's reports only, which Podio shows inline
- `target-field`, `child`, `status: proposed`, versions: as now

The review queue groups by child and then by `target-field`, so a reviewer opening one
young person's triggers sees the groups as separate items side by side, each with its own
evidence and links.

### Why

- **Review per group.** The reviewer can accept one group, reject another and edit a third.
  Each decision is recorded separately, which is far better data for an accuracy claim
  (spec §6) than one verdict on a mixed box.
- **"Never re-propose a rejected value" gets more precise** (spec §5, safety rule 2).
  Rejecting one group no longer blocks the whole field's proposal next time.
- **Drafts stay short.** Each item is one line, which fits spec §1: easy to change, not easy
  to accept.
- **The volume fits the spec.** About 4 fields × 2–4 groups is roughly 8–15 items per child,
  inside the "5–15 per child per batch" in spec §5.
- **No schema change.** Same fields, more and smaller items. `write.py` doesn't change.

### Costs

- **Spec §5 changes** from "one proposal per (child, target-field)" to one per (child,
  target-field, group).
- **Applying accepted proposals takes one more manual step.** The profile field holds one
  text value, so whoever applies accepted proposals combines the accepted groups' lines into
  it. That step is manual anyway (spec §6, "Applying an accepted proposal to the profile").
- **A wrong merge looks more convincing.** If two different observations end up in one
  group, that item shows a high evidence count for what is really two things. This is the
  merging step's over-merging risk (see 3), made more important here: merge only when items
  describe the same thing, and score wrong merges heavily in the eval.

### What changes in code

- `aggregate.py`: return one `Proposal` per group instead of one per field. Ranking and
  `MAX_PHRASES` no longer pick phrases for a joined draft. They might instead cap how many
  groups per field are proposed, keeping the most evidenced.
- Spec §5 and §6: the aggregation rule and the review workflow description.
- `write.py`, `models.Proposal`: no change.

## 5. Deferred from the MVP

Each of these is referenced in project.md. They're listed here so the whole backlog is in one
place.

### Build

- **`vocabulary.py`** (spec §13): curated phrasings per category option, with relevant
  examples fed into the prompt. It needs accepted review decisions to exist first, and should
  mine rejections as negative examples. It is also the longer-term help with relevance (spec
  §5, quote verification), and the natural home for the activity areas in 1.
- **Measure time spent on each proposal** (spec §3, §6 review telemetry). Not a field on the
  proposal, since anything on the item is visible to everyone who can open it, and the
  measure must never be shown to instructors or used on individuals. Compute it offline from
  Podio's revision timestamps (the gap between one reviewer's consecutive status changes),
  report only in aggregate, and never write it back to Podio. Open: whether that proxy is good
  enough. The live sandbox app still has a `review-duration` field to delete by hand.
- **Compare models in the eval harness** (spec §10). `eval/score.py` runs the one configured
  model. Looping over candidates (e.g. 3B vs 8B) is what turns hardware needs into evidence.
- **Other category-field targets** (spec §3). Activities are proposed in 1. Ask coordinators
  which of the other profile fields could also be category fields.
- **Automate applying accepted proposals** (spec §6): a Podio workflow on `status →
  accepted/edited` that writes `final-value` (or `proposed-value`) into the profile. Only once
  the mapping from target field to profile field is stable and trusted.
- **Model call timeout.** One call in the first run hit the 5-minute timeout (spec appendix,
  finding 10), probably the model generating without stopping. If it recurs, cap the reply
  length or shorten the timeout.
- **Track processed reports in Podio**, only if recomputing every run ever becomes too slow
  (spec §5). Never locally.

### Before any real data

- **Remove `run.py --save`** (spec §5, second exception).
- **Verify permission filtering with a second Podio user** (spec §9). Not possible on the free
  sandbox plan.
- **Revisit `evidence-quotes`** if the deployment restricts daily reports per user (spec §2,
  constraint 4). Copying quotes is only acceptable where every worker can see every report.

### Smaller items

- **Never re-propose a rejected value** (spec §5, batch run safety rule 2). Needs one more
  Podio query per run, to the proposals app, for the child's rejected items.
- **Show "of m sessions" to the reviewer.** It was lost when the `title` field was dropped
  (spec §6, MVP gaps). `evidence-count` holds only n.
- **Current profile values.**  `aggregate.drop_current` currently has nothing to compare against.
- **Check Podio's hourly rate limit.** The response header suggested 250 calls per hour on
  this account, not the 1,000 in spec §7.
- **Split out `schema.py`** once a second module reads the same Podio field types (spec
  §13).
- **App-token authentication for writes**, so the script can only ever touch Suggested
  Profile Updates. Optional hardening. It conflicts with spec §2 constraint 2 (writes
  attributed to the coordinator), so it needs a decision.
