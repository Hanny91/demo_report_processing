# YW_reports

A prototype that reads youth workers' daily session reports in [Podio](https://podio.com) and
proposes updates to each young person's profile, every proposal traceable to the exact
sentence it came from. A worker reviews and decides, inside Podio.

**Status:** proof of concept, run end to end on mock data only. Not for real data: it handles
records about vulnerable young people, and the safeguards it needs for that are designed but
not all built (see [Further steps](further-steps.md)).

## How it works

```
Podio reports ──► local LLM, one call per (report, field) ──► quote check ──► aggregate ──► Podio proposals app
```

- **Everything runs locally.** The model runs on your machine through [Ollama](https://ollama.com).
  No report text goes to any external API.
- **Never auto-writes.** Proposals go into a separate Podio app, `Suggested Profile Updates`,
  with status `proposed`. A human accepts, edits or rejects them there. Nothing writes to the
  profile itself.
- **Every proposal cites its source.** The model must quote the sentence it relied on. If the
  quote isn't in the report (exactly, apart from typography), the extraction is discarded.
- **Nothing is stored locally.** Each run rebuilds from Podio. The exceptions are
  development-only and opt-in (see [project.md](project.md) §5).

The full design and its reasoning are in [project.md](project.md).

## Layout

```
yw/
  extract/   prompt.py, llm.py (the only model call), parse.py, verify.py (the quote check)
  podio/     client.py (OAuth), read.py, write.py
  aggregate.py, models.py, config.py
  run.py     one run for one child: the only module with side effects
eval/        cases.yaml + score.py: offline extraction benchmark on mock reports
tests/       python -m pytest
podio_api_work/  one-off scripts: OAuth spike, mock data seeding, app provisioning
```

## Setup

Needs Python 3.12+, Ollama, and a Podio account with a reports app and a profile app.

1. **Packages:** `pip install pyyaml pytest`. The main code uses only the standard library.
   The scripts in `podio_api_work/` also need `pip install -r podio_api_work/requirements.txt`.
2. **Model:** `ollama pull llama3.1:8b`, then `ollama create llama3.1-8b-cpu -f Modelfile-cpu`.
   The CPU-only copy avoids an Ollama crash on small GPUs. To use another model, change
   `MODEL_NAME` in `yw/config.py`.
3. **Podio:** register an API client at <https://podio.com/settings/api>, then copy
   `.env.example` to `.env` and fill it in.
4. **Proposals app:** `python podio_api_work/setup_podio_app.py` creates it. Put its id in
   `PODIO_PROPOSALS_APP_ID`.

## Run

```bash
python -m yw.run --child <profile item_id>                    # dry run: reads and extracts, writes nothing
python -m yw.run --child <profile item_id> --write            # create proposals in Podio
python -m yw.run --child <profile item_id> --max-reports 1 --write   # quick end-to-end check
python -m eval.score                                          # extraction benchmark
python -m pytest                                              # tests, no network needed
```

The first run opens a browser to sign in to Podio. Logs (ids and counts only, never report
text) go to `runs/`. On a CPU, expect roughly 45 seconds per model call, so a few minutes per
report.

## Documents

- [project.md](project.md): design, constraints, and findings from the build. Code comments
  cite its sections as "spec §N".
- [further-steps.md](further-steps.md): what comes next
