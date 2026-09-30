"""
Builds the one prompt per (report, target field) that llm.complete sends to
the model (spec §13: one field per call, never several fields in one pass —
small models extract far more accurately from one narrow question).

The contract with parse.py is fixed here: the model is told to reply with
exactly

    {"extractions": [{"value": "...", "quote": "..."}, ...]}

and that {"extractions": []} is the correct answer when a report has
nothing to say about the field (spec §1, §3 — "no" is an acceptable result,
not a defect). parse.py's tolerance for format noise (code fences, stray
prose, Python literals) exists because small models drift from this
instruction despite being told; nothing here should be read as a guarantee
they'll comply.
"""

from yw.models import Report, TargetField

_FIELD_GUIDANCE: dict[TargetField, str] = {
    TargetField.PRESENTATION: (
        "How the young person presented in this session — mood, engagement, "
        "energy, how they related to staff and peers."
    ),
    TargetField.BOUNDARIES: (
        "Descriptive handover guidance about limits or boundaries this young "
        "person needs, for whoever next works with them — not a formal risk "
        "assessment. For example: gets overwhelmed by loud groups, needs a "
        "warning before transitions."
    ),
    TargetField.TRIGGERS: (
        "Specific things in this session that triggered distress, "
        "withdrawal, or dysregulation for this young person, and what "
        "happened as a result."
    ),
    TargetField.PROJECTS_AND_ACTIVITIES: (
        "Named projects or activities the young person worked on or took "
        "part in during this session, and anything notable about how it went."
    ),
}

_SYSTEM_PROMPT = """\
You are helping a youth work organisation read daily session reports written \
by outdoor education instructors about young people in their care.

For the ONE field you are asked about below, find every sentence in the \
report that supports a value for that field. Only use what the report \
actually says — never guess, infer beyond the text, or fill in something \
plausible. If the report says nothing about this field, that is a normal, \
expected answer.

Reply with ONLY this JSON shape, nothing else — no prose, no markdown fences:

{"extractions": [{"value": "...", "quote": "..."}]}

- "value" is a short phrase, not a sentence, describing what the report \
supports.
- "quote" is copied EXACTLY from the report: the precise sentence "value" is \
drawn from, with no paraphrasing, no fixed spelling, no changed wording. It \
must be a verbatim substring of the report text.
- If several separate things support the field, include one entry per thing.
- If nothing in the report supports this field, reply {"extractions": []}. \
Do not force an answer."""


def build_prompt(
    report: Report, field: TargetField, examples: tuple[str, ...] = ()
) -> list[dict[str, str]]:
    """
    Build the chat messages for one (report, field) model call (spec §13).

    `examples` is curated phrasing from yw/vocabulary.py, once that module
    exists (deferred — spec §13, "Deferred: vocabulary.py"). Omit it until
    then; build_prompt works with none.
    """
    lines = [
        f"Field: {field.value}",
        f"What to look for: {_FIELD_GUIDANCE[field]}",
    ]
    if examples:
        lines.append("Examples of how this field has been phrased before:")
        lines.extend(f"- {example}" for example in examples)
    lines.append("")
    lines.append("Report:")
    lines.append(report.text)

    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]
