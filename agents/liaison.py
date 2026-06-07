"""Phase 3: Agency Liaison — dispatched voice agent that "calls" an agency, briefs
it on the incident, gathers guidance, and submits a FINDING to the event bus.

One liaison per agency (2 for the demo: Fire/HazMat + Poison Control). The liaison:
  1. Joins its sub-room (created by connect_party in the supervisor).
  2. Dispatches the Agency Sim into the same sub-room.
  3. Opens with a briefing (generate_reply); the two agents have a voice conversation.
  4. Calls submit_finding() once it has clear guidance → FINDING on the event bus.
  5. The supervisor relays that finding to the 911 caller.
"""

from __future__ import annotations

import asyncio
import json
import logging
import textwrap

from livekit.agents import Agent, AgentSession, RunContext, function_tool, inference
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.protocol.agent_dispatch import CreateAgentDispatchRequest

import seed_data
import state
import voices
from llm import build_llm
from state import EventType

logger = logging.getLogger("liaison")

ORCHESTRATOR_NAME = "dispatch-orchestrator"
_LIAISON_TIMEOUT_S = 90
_SIM_JOIN_WAIT_S = 2  # give the agency-sim time to join before the liaison speaks


def _instructions(agency: dict, facts: dict) -> str:
    facts_lines = "\n".join(f"      - {k}: {v}" for k, v in facts.items())
    if not facts_lines:
        facts_lines = "      - (still gathering details)"
    return textwrap.dedent(f"""\
        You are a 911 emergency dispatch liaison reaching out to {agency['name']} on behalf
        of the dispatch center. You have just dialed them and they have answered.

        Confirmed incident details:
{facts_lines}

        Your job:
        1. Introduce yourself briefly ("Emergency Dispatch calling about an active incident…").
        2. Relay the key incident details clearly and concisely.
        3. Ask for their specific guidance (isolation distances, medical protocols, etc.).
        4. Once you have received clear, actionable guidance, call `submit_finding` with a
           concise one-paragraph summary of their advice, then conclude the call politely.

        Speak in plain sentences — no markdown, lists, or code. Keep each turn to 2–3
        sentences. This is an active emergency; be efficient.
    """)


class LiaisonAgent(Agent):
    def __init__(
        self,
        incident: state.IncidentState,
        agency: dict,
        done_event: asyncio.Event,
    ) -> None:
        super().__init__(instructions=_instructions(agency, incident.facts), llm=build_llm())
        self._incident = incident
        self._agency = agency
        self._done_event = done_event

    @function_tool()
    async def submit_finding(self, context: RunContext, summary: str) -> str:
        """Submit the agency's guidance as a finding to the incident event bus.

        Call this once you have received clear, actionable guidance from the agency.
        After calling this, conclude the call politely.

        Args:
            summary: concise paragraph summarising the agency's guidance
        """
        agency_id = self._agency["id"]
        self._incident.add_finding(agency_id, summary)
        asyncio.create_task(
            self._incident.emit(EventType.FINDING, source=agency_id, summary=summary)
        )
        logger.info("liaison submitted finding from %s: %.80s", agency_id, summary)

        # Set done after a short pause so the liaison has time to say goodbye via TTS.
        async def _deferred_done() -> None:
            await asyncio.sleep(6)
            self._done_event.set()

        asyncio.create_task(_deferred_done())
        return "Finding submitted. You may now conclude the call."


async def run_liaison(ctx, meta: dict) -> None:
    incident_id = meta.get("incident_id")
    agency_id = meta.get("agency_id")

    incident = state.get_incident(incident_id)
    if incident is None:
        logger.error("liaison: no incident found for id=%r", incident_id)
        return

    agency = next((a for a in seed_data.agencies() if a["id"] == agency_id), None)
    if agency is None:
        logger.error("liaison: unknown agency_id %r", agency_id)
        return

    logger.info("liaison starting: agency=%s room=%s", agency_id, ctx.room.name)

    done_event = asyncio.Event()

    session = AgentSession(
        stt=inference.STT(model="deepgram/nova-3", language="multi"),
        tts=inference.TTS(model="cartesia/sonic-3", voice=voices.get_voice("liaison")),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
    )

    await session.start(agent=LiaisonAgent(incident, agency, done_event), room=ctx.room)
    await ctx.connect()

    # Dispatch the Agency Sim into the same sub-room.
    dispatch = await ctx.api.agent_dispatch.create_dispatch(
        CreateAgentDispatchRequest(
            agent_name=ORCHESTRATOR_NAME,
            room=ctx.room.name,
            metadata=json.dumps({
                "role": "agency_sim",
                "agency_id": agency_id,
                "incident_id": incident_id,
            }),
        )
    )
    logger.info("dispatched agency_sim for %s: dispatch_id=%s", agency_id, dispatch.id)

    # Give the agency sim a moment to join before the liaison starts speaking.
    await asyncio.sleep(_SIM_JOIN_WAIT_S)

    facts_summary = "; ".join(f"{k}={v}" for k, v in incident.facts.items())
    await session.generate_reply(
        instructions=(
            f"You are calling {agency['name']}. They have just answered. "
            f"Brief them on this hazmat incident: {facts_summary}. "
            "Introduce yourself as 911 Emergency Dispatch and relay the key details concisely."
        )
    )

    # Hold the job alive until submit_finding() signals completion (or timeout).
    try:
        await asyncio.wait_for(done_event.wait(), timeout=_LIAISON_TIMEOUT_S)
    except asyncio.TimeoutError:
        logger.warning("liaison timed out after %ds for %s", _LIAISON_TIMEOUT_S, agency_id)
    finally:
        await session.aclose()
