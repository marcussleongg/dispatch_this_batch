"""Phase 3: Agency Liaison — dispatched voice agent that "calls" an agency, briefs
it on the incident, gathers guidance, and submits a FINDING back to the supervisor.

One liaison per agency (2 for the demo: Fire/HazMat + Poison Control). The liaison
runs in a separate OS process from the orchestrator (LiveKit spawns a fresh worker
process per dispatched job), so it cannot share in-memory state. Instead:
  - Facts come from dispatch metadata (snapshot at dispatch time).
  - Findings are published to the main room via LiveKit's server-side send_data API;
    the supervisor's data_received handler on the main room picks them up.
"""

from __future__ import annotations

import asyncio
import json
import logging
import textwrap
import time

from livekit.agents import Agent, AgentSession, RunContext, function_tool, inference
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.protocol.agent_dispatch import CreateAgentDispatchRequest
from livekit.protocol.room import SendDataRequest

import seed_data
import voices
from llm import build_llm
from tools.live_conditions_cloud import query_live_index

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
        1. Call `lookup_live_conditions` with "current conditions wind weather" to get any
           live situational data (wind direction, weather) before you start speaking.
        2. Introduce yourself briefly ("Emergency Dispatch calling about an active incident…").
        3. Relay the key incident details clearly and concisely, including any live conditions.
        4. Ask for their specific guidance (isolation distances, medical protocols, etc.).
        5. Once you have received clear, actionable guidance, call `submit_finding` with a
           concise one-paragraph summary of their advice, then conclude the call politely.

        Speak in plain sentences — no markdown, lists, or code. Keep each turn to 2–3
        sentences. This is an active emergency; be efficient.
    """)


class LiaisonAgent(Agent):
    def __init__(
        self,
        agency: dict,
        facts: dict,
        done_event: asyncio.Event,
        ctx,        # JobContext — used to publish the finding to the main room
        main_room: str,
        incident_id: str = "",
    ) -> None:
        super().__init__(instructions=_instructions(agency, facts), llm=build_llm("liaison"))
        self._agency_id = agency["id"]
        self._done_event = done_event
        self._ctx = ctx
        self._main_room = main_room
        self._incident_id = incident_id

    @function_tool()
    async def lookup_live_conditions(self, context: RunContext, query: str) -> str:
        """Query live conditions gathered during this incident: wind direction,
        weather, agency dispatch status, or any web research findings.

        Use this before briefing the agency or when you need situational context
        that wasn't in the initial facts (e.g. current wind, evacuation zones).

        Args:
            query: what you need, e.g. "wind direction", "current weather conditions"
        """
        if not self._incident_id:
            return "Live conditions not available."
        result = await query_live_index(self._incident_id, query)
        return result if result else "No live conditions data available yet."

    @function_tool()
    async def submit_finding(self, context: RunContext, summary: str) -> str:
        """Submit the agency's guidance as a finding to the incident event bus.

        Call this once you have received clear, actionable guidance from the agency.
        After calling this, conclude the call politely.

        Args:
            summary: concise paragraph summarising the agency's guidance
        """
        logger.info("liaison submitting finding from %s: %.80s", self._agency_id, summary)

        # Publish the finding to the main orchestrator room via the LiveKit server API.
        # The supervisor's data_received handler on that room will pick it up and relay
        # it to the caller. This crosses the process boundary cleanly.
        payload = json.dumps({
            "type": "finding",
            "source": self._agency_id,
            "summary": summary,
        }).encode()
        try:
            await self._ctx.api.room.send_data(
                SendDataRequest(room=self._main_room, data=payload, topic="finding")
            )
            logger.info("finding published to main room %s", self._main_room)
        except Exception:
            logger.exception("failed to publish finding to main room")

        # Defer done so the liaison has time to say goodbye via TTS before we close.
        async def _deferred_done() -> None:
            await asyncio.sleep(6)
            self._done_event.set()

        asyncio.create_task(_deferred_done())
        return "Finding submitted. You may now conclude the call."


async def run_liaison(ctx, meta: dict) -> None:
    agency_id = meta.get("agency_id")
    main_room = meta.get("main_room", "")
    incident_id = meta.get("incident_id", "")
    # Facts were snapshotted into metadata at dispatch time — no registry needed.
    facts = meta.get("facts") or {}

    agency = next((a for a in seed_data.agencies() if a["id"] == agency_id), None)
    if agency is None:
        logger.error("liaison: unknown agency_id %r", agency_id)
        return

    logger.info("liaison starting: agency=%s room=%s main_room=%s", agency_id, ctx.room.name, main_room)

    done_event = asyncio.Event()

    session = AgentSession(
        stt=inference.STT(model="deepgram/nova-3", language="multi"),
        tts=inference.TTS(model="cartesia/sonic-3", voice=voices.get_voice("liaison")),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
    )

    await session.start(
        agent=LiaisonAgent(agency, facts, done_event, ctx, main_room, incident_id=incident_id),
        room=ctx.room,
    )
    await ctx.connect()

    # Dispatch the Agency Sim into the same sub-room, with the facts snapshot so
    # it can look up accurate chemical data rather than improvising.
    dispatch = await ctx.api.agent_dispatch.create_dispatch(
        CreateAgentDispatchRequest(
            agent_name=ORCHESTRATOR_NAME,
            room=ctx.room.name,
            metadata=json.dumps({
                "role": "agency_sim",
                "agency_id": agency_id,
                "facts": facts,
            }),
        )
    )
    logger.info("dispatched agency_sim for %s: dispatch_id=%s", agency_id, dispatch.id)

    # Notify the dashboard that the agency sim tile should appear.
    sim_payload = json.dumps({
        "type": "worker_status",
        "ts": time.time(),
        "incident_id": meta.get("incident_id", ""),
        "worker_id": f"sim_{agency_id}",
        "label": agency["name"] + " (agency)",
        "status": "active",
        "sub_room": ctx.room.name,
        "parent_id": f"agency_{agency_id}",
    }).encode()
    try:
        await ctx.api.room.send_data(
            SendDataRequest(room=main_room, data=sim_payload, topic="dashboard")
        )
    except Exception:
        logger.exception("failed to publish sim worker_status to main room")

    # Give the agency sim a moment to join before the liaison starts speaking.
    await asyncio.sleep(_SIM_JOIN_WAIT_S)

    facts_summary = "; ".join(f"{k}={v}" for k, v in facts.items()) or "details being gathered"
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
