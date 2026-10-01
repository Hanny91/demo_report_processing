"""
The only function in this codebase that calls a model (spec §13). Every
other module is plain data transformation. check_ready() only lists
installed models, it doesn't call one.

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


def check_ready() -> None:
    """
    Check Ollama is reachable and has MODEL_NAME, without calling the model:
    GET /models only lists what's installed. Raises LLMError saying what to
    fix, so a long run fails at the start rather than on every call.
    """
    try:
        with urllib.request.urlopen(f"{BASE_URL}/models", timeout=10) as response:
            data = json.load(response)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise LLMError(f"Ollama isn't reachable at {BASE_URL} ({exc}); start it and retry") from exc

    installed = {m.get("id") for m in data.get("data", []) if isinstance(m, dict)}
    # Ollama treats an untagged name as ":latest", and lists it that way.
    if MODEL_NAME not in installed and f"{MODEL_NAME}:latest" not in installed:
        raise LLMError(f"model {MODEL_NAME!r} isn't installed; run: ollama pull {MODEL_NAME}")


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
    except urllib.error.HTTPError as exc:
        raise LLMError(
            f"request to model {MODEL_NAME!r} failed: {exc.code} {_ollama_error(exc)}"
        ) from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise LLMError(f"request to model {MODEL_NAME!r} failed: {exc}") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("reply had no usable content") from exc

    if not isinstance(content, str) or not content.strip():
        raise LLMError("reply content was empty")

    return content


def _ollama_error(exc: urllib.error.HTTPError) -> str:
    """
    Ollama's own reason for an error response, e.g. a model that couldn't
    load. It describes the server or model, never the prompt. Capped anyway.
    """
    try:
        error = json.loads(exc.read()).get("error", "")
    except (ValueError, AttributeError, OSError):
        return ""
    # The /v1 endpoint nests it OpenAI-style: {"error": {"message": "..."}}.
    if isinstance(error, dict):
        error = error.get("message", "")
    return error[:200] if isinstance(error, str) else ""
