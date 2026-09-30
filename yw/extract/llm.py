"""
The only function in this codebase that calls a model (spec §13). Every
other module is plain data transformation.

Talks to Ollama's own OpenAI-compatible endpoint (spec §2 constraint 1:
local models only, everywhere — llm.py never points at a cloud endpoint, in
run.py or in the eval harness). Plain HTTP to localhost, stdlib only: no
third-party HTTP client is needed for a call that never leaves this machine.

One signature, forever: messages in, string out. Parsing the reply belongs
to parse.py, not here — this function does not know or care what shape the
content is in.
"""

import json
import urllib.error
import urllib.request

from yw.config import BASE_URL, MODEL_NAME, TEMPERATURE

# Small local models on CPU can be slow, especially the first call against a
# prompt Ollama hasn't cached yet (measured: a cold call took ~2 minutes on
# an 8B model running mostly on CPU, a repeat of the same prompt ~47s).
# Podio's own rate limit (spec §7) is per-hour, not per-call, so there's no
# reason to cut this short.
TIMEOUT = 300.0


class LLMError(RuntimeError):
    """
    The call to the model failed: couldn't reach Ollama, a non-2xx response,
    or a reply with no usable content. Never includes the request or reply
    text (spec §5 — never log field values or report text).
    """


def complete(messages: list[dict[str, str]]) -> str:
    """
    Send `messages` (OpenAI chat-format) to the configured local model and
    return its reply text. Temperature is always taken from config.py,
    never left to Ollama's default. Raises LLMError on any failure.
    """
    body = json.dumps(
        {"model": MODEL_NAME, "messages": messages, "temperature": TEMPERATURE}
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = json.load(response)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise LLMError(f"request to model {MODEL_NAME!r} failed: {exc}") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("reply had no usable content") from exc

    if not isinstance(content, str) or not content.strip():
        raise LLMError("reply content was empty")

    return content
