"""LLM selection — provider + model fully controlled by env vars.

Switch providers by setting LLM_PROVIDER; models are resolved per-role so each
agent in the simulation can use a different model without code changes.

Resolution order for each role:
  LLM_<ROLE>_MODEL  →  LLM_MODEL  →  provider hardcoded default

Providers
---------
inference   (default) LiveKit Inference — no extra keys, just LIVEKIT_* creds.
truefoundry OpenAI-compatible gateway (MiniMax / Qwen or any self-hosted model).
            Requires TRUEFOUNDRY_BASE_URL + TRUEFOUNDRY_API_KEY.
            Install: uv sync --extra truefoundry

Example configs
---------------
# All inference, default model — set nothing.

# TrueFoundry, one model for everything:
#   LLM_PROVIDER=truefoundry
#   LLM_MODEL=qwen/Qwen
#   TRUEFOUNDRY_BASE_URL=https://gateway.truefoundry.ai
#   TRUEFOUNDRY_API_KEY=...

# TrueFoundry, per-side of the simulation:
#   LLM_PROVIDER=truefoundry
#   LLM_CALL_TAKER_MODEL=qwen/Qwen
#   LLM_LIAISON_MODEL=qwen/Qwen
#   LLM_AGENCY_SIM_MODEL=qwen/minimax
#   TRUEFOUNDRY_BASE_URL=...
#   TRUEFOUNDRY_API_KEY=...
"""

from __future__ import annotations

import logging
import os

from livekit.agents import inference
from livekit.agents.llm import LLM

logger = logging.getLogger("llm")

_INFERENCE_DEFAULT = os.getenv("INFERENCE_LLM_MODEL", "openai/gpt-5.2-chat-latest")

# Import the OpenAI plugin at module load time (main thread) so LiveKit's plugin
# registry requirement is satisfied. Wrapped in try/except so inference-only
# setups that haven't run `uv sync --extra truefoundry` don't break.
try:
    from livekit.plugins import openai as lk_openai
    _HAS_OPENAI_PLUGIN = True
except ImportError:
    lk_openai = None  # type: ignore[assignment]
    _HAS_OPENAI_PLUGIN = False


def _resolve_model(role: str, provider_default: str) -> str:
    """Resolve model for a role: role-specific → global → provider default.

    Strips values so that empty vars (LLM_CALL_TAKER_MODEL=) are ignored.
    """
    for key in (f"LLM_{role.upper()}_MODEL", "LLM_MODEL"):
        val = (os.getenv(key) or "").strip()
        if val:
            return val
    return provider_default


def build_fallback_llm(role: str = "default") -> LLM | None:
    """Return a fallback LLM for 400 errors, or None if LLM_FALLBACK_MODEL is unset."""
    model = (os.getenv(f"LLM_{role.upper()}_FALLBACK_MODEL") or
             os.getenv("LLM_FALLBACK_MODEL", "")).strip()
    if not model:
        return None
    provider = os.getenv("LLM_PROVIDER", "inference").lower()
    logger.debug("build_fallback_llm role=%s provider=%s model=%s", role, provider, model)
    if provider == "truefoundry":
        if not _HAS_OPENAI_PLUGIN:
            raise RuntimeError(
                "livekit-plugins-openai is not installed. Run: uv sync --extra truefoundry"
            )
        base_url = os.getenv("TRUEFOUNDRY_BASE_URL", "").strip()
        api_key = os.getenv("TRUEFOUNDRY_API_KEY", "").strip()
        if not base_url or not api_key:
            raise RuntimeError(
                "LLM_PROVIDER=truefoundry requires TRUEFOUNDRY_BASE_URL and TRUEFOUNDRY_API_KEY"
            )
        return lk_openai.LLM(model=model, base_url=base_url, api_key=api_key)
    return inference.LLM(model=model)


def build_llm(role: str = "default") -> LLM:
    """Return the configured LLM for the given agent role.

    Args:
        role: "call_taker" | "liaison" | "agency_sim" | "default"
    """
    provider = os.getenv("LLM_PROVIDER", "inference").lower()

    if provider == "truefoundry":
        if not _HAS_OPENAI_PLUGIN:
            raise RuntimeError(
                "livekit-plugins-openai is not installed. Run: uv sync --extra truefoundry"
            )
        base_url = os.getenv("TRUEFOUNDRY_BASE_URL", "").strip()
        api_key = os.getenv("TRUEFOUNDRY_API_KEY", "").strip()
        if not base_url or not api_key:
            raise RuntimeError(
                "LLM_PROVIDER=truefoundry requires TRUEFOUNDRY_BASE_URL and TRUEFOUNDRY_API_KEY"
            )
        model = _resolve_model(role, provider_default="")
        if not model:
            raise RuntimeError(
                f"No model configured for role '{role}'. "
                f"Set LLM_MODEL or LLM_{role.upper()}_MODEL."
            )
        logger.debug("build_llm role=%s provider=truefoundry model=%s", role, model)
        return lk_openai.LLM(model=model, base_url=base_url, api_key=api_key)

    # Default: LiveKit Inference
    model = _resolve_model(role, provider_default=_INFERENCE_DEFAULT)
    logger.debug("build_llm role=%s provider=inference model=%s", role, model)
    return inference.LLM(model=model)
