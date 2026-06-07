"""Phase 3: Agency (sim) — AI role-playing an agency (Poison Control, Fire/HazMat)
on the other end of the liaison's sub-call. Joins the same sub-room as the liaison;
has its own distinct voice, audible on the dashboard. Never heard by the 911 caller.

Receives incident facts via metadata so it can inject accurate ERG chemical data
(isolation distances, hazards, actions) from the seed fallback into its instructions,
rather than having the LLM improvise numbers.
"""

from __future__ import annotations

import asyncio
import logging
import re
import textwrap

from livekit.agents import Agent, AgentSession, inference
from livekit.plugins.turn_detector.multilingual import MultilingualModel

import seed_data
import voices
from llm import build_llm

logger = logging.getLogger("agency_sim")

_SIM_TIMEOUT_S = 120
_UN_RE = re.compile(r"\b(\d{4})\b")


def _chemical_context(facts: dict) -> str:
    """Look up the chemical record from seed data and format it for the instructions."""
    un_raw = facts.get("un_number") or facts.get("chemical") or ""
    match = _UN_RE.search(str(un_raw))
    if not match:
        return ""
    rec = seed_data.chemicals().get(match.group(1))
    if not rec:
        return ""
    return textwrap.dedent(f"""\

        Verified chemical data for this incident (ERG Guide {rec.get('erg_guide')}):
        - UN {rec.get('un')}: {rec.get('name')}
        - Hazards: {rec.get('hazards', '')}
        - Initial isolation: {rec.get('initial_isolation_ft')} ft in all directions
        - Protective evacuation: {rec.get('protective_evacuation_mi')} mile(s) downwind
        - Recommended actions: {rec.get('actions', '')}

        Use these exact figures when giving guidance — do not invent different numbers.
    """)


def _instructions(agency: dict, facts: dict) -> str:
    chemical_block = _chemical_context(facts)
    return textwrap.dedent(f"""\
        You are {agency['name']}. {agency['blurb']}

        A 911 dispatch liaison is calling you about an active hazmat emergency. You have
        just picked up the phone. Listen to their briefing, then respond immediately with
        specific, authoritative guidance tailored to your agency's role:

        - Fire / HazMat: isolation perimeter, PPE required, suppression approach,
          evacuation zone, containment steps.
        - Poison Control: decontamination steps, symptoms to monitor, medical
          observation period, whether to alert hospitals.
        - Public Works: road closure points, traffic diversion routes, infrastructure risks.
        {chemical_block}
        Keep each response to 3–4 sentences. Be direct and professional — this is an
        active emergency. Do not ask unnecessary clarifying questions; provide actionable
        guidance based on what the dispatcher tells you. Speak in plain sentences as if
        on a phone call — no markdown, lists, or code.
    """)


class AgencySimAgent(Agent):
    def __init__(self, agency: dict, facts: dict) -> None:
        super().__init__(instructions=_instructions(agency, facts), llm=build_llm())


async def run_agency_sim(ctx, meta: dict) -> None:
    agency_id = meta.get("agency_id", "unknown")
    facts = meta.get("facts") or {}

    agency = next((a for a in seed_data.agencies() if a["id"] == agency_id), None)
    if agency is None:
        logger.error("agency_sim: unknown agency_id %r", agency_id)
        return

    logger.info("agency_sim starting: agency=%s room=%s", agency_id, ctx.room.name)

    session = AgentSession(
        stt=inference.STT(model="deepgram/nova-3", language="multi"),
        tts=inference.TTS(model="cartesia/sonic-3", voice=voices.get_voice(agency_id)),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
    )

    await session.start(agent=AgencySimAgent(agency, facts), room=ctx.room)
    await ctx.connect()

    # Hold the job alive until the liaison closes the room or the safety-net fires.
    try:
        await asyncio.sleep(_SIM_TIMEOUT_S)
    finally:
        await session.aclose()
