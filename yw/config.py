"""
Model configuration (spec §13): model name, base_url, temperature, prompt
version. Plain constants — nothing here reads the environment or touches
the network.

Local models only, everywhere (spec §2 constraint 1, §10, §13): base_url
always points at Ollama on this machine, never a cloud endpoint. Change
MODEL_NAME to compare candidates; the eval harness loops over candidates,
deployment pins one.
"""

MODEL_NAME = "llama3.1-8b-cpu"

# Ollama's own OpenAI-compatible endpoint (spec §13). Never anything else.
BASE_URL = "http://localhost:11434/v1"

# Set explicitly on every call (spec §13) — never rely on Ollama's sampling
# defaults, which vary between versions and models and would silently
# invalidate comparisons between them.
TEMPERATURE = 0.0

# Bumped whenever prompt.py's wording changes, so a shift in extraction
# quality can be attributed to the prompt rather than confused with a model
# change (spec §6).
PROMPT_VERSION = "v1"
