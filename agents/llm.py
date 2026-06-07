"""LLM selection for the orchestrator (and, later, every dispatched agent).

Two paths, picked by env:

* **LiveKit Inference (default)** — `inference.LLM("openai/gpt-5.2-chat-latest")`.
  No provider key; only LIVEKIT_* creds. This is the moss-hacker-starter path and
  the fastest "call in and talk" setup.
* **TrueFoundry gateway (locked plan decision)** — when TRUEFOUNDRY_BASE_URL +
  TRUEFOUNDRY_API_KEY are set, route through the OpenAI-compatible gateway so LLM
  calls hit MiniMax / Qwen with governance + observability. Flip = set two env vars
  (and `uv sync --extra truefoundry`); no code change.

Per-role callers can override the model (MiniMax vs Qwen) via ``model``.
"""

from __future__ import annotations

import os

from livekit.agents import inference
from livekit.agents.llm import LLM

# Default LiveKit Inference model (same family the starter ships).
INFERENCE_LLM = os.getenv("INFERENCE_LLM_MODEL", "openai/gpt-5.2-chat-latest")


def build_llm(model: str | None = None) -> LLM:
    """Return the configured LLM: TrueFoundry gateway if set, else LiveKit Inference."""
    base_url = os.getenv("TRUEFOUNDRY_BASE_URL") or None
    api_key = os.getenv("TRUEFOUNDRY_API_KEY") or None

    if base_url and api_key:
        # OpenAI-compatible gateway (MiniMax / Qwen). Lazy import so the openai
        # plugin is only required when the TrueFoundry seam is actually used.
        from livekit.plugins import openai

        return openai.LLM(
            model=model or os.getenv("TRUEFOUNDRY_MODEL", "minimax"),
            base_url=base_url,
            api_key=api_key,
        )

    return inference.LLM(model=model or INFERENCE_LLM)
