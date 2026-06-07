"""Phase 2+3: Supervisor — event-driven asyncio control loop (same process).

Subscribes to the IncidentState event bus and OWNS ALL dispatch of action agents.
The loop only ever awaits the queue — dispatch decisions are synchronous and relay
actions are fire-and-forget tasks, so new FACT events are never blocked by an
in-progress relay.
"""

from __future__ import annotations

import asyncio
import json
import logging

from livekit.agents import AgentSession

import seed_data
from connect import connect_party
from state import EventType, IncidentState
from web_research import web_research

logger = logging.getLogger("supervisor")


def _should_dispatch_agency(agency: dict, facts: dict) -> bool:
    aid = agency["id"]
    has_chemical = "un_number" in facts or "chemical" in facts
    if aid == "fire_hazmat":
        return has_chemical
    if aid == "poison_control":
        return has_chemical and "injuries" in facts
    return False


class Supervisor:
    def __init__(self) -> None:
        self._dispatched: set[str] = set()
        self._ctx = None
        self._session: AgentSession | None = None

    async def run(self, incident: IncidentState, session: AgentSession, ctx) -> None:
        self._ctx = ctx
        self._session = session
        logger.info("Supervisor started for incident %s", incident.incident_id)

        # Receive findings published by liaison agents (separate OS processes) via
        # LiveKit data messages. Liaisons can't write to our asyncio.Queue directly,
        # so they call ctx.api.room.send_data() on this room instead.
        ctx.room.on("data_received", self._on_data_received)

        while True:
            event = await incident.events.get()   # only await in the loop
            if event.type == EventType.FACT:
                self._on_fact(incident)            # synchronous — never blocks
            elif event.type == EventType.FINDING:
                asyncio.create_task(               # fire and forget
                    self._relay_finding(session, event.payload)
                )

    def _on_data_received(self, data_packet) -> None:
        """Handle findings published by liaison agents via LiveKit data messages."""
        try:
            payload = json.loads(bytes(data_packet.data).decode())
            if payload.get("type") == "finding" and self._session is not None:
                logger.info(
                    "data_received finding from %s: %.80s",
                    payload.get("source"),
                    payload.get("summary", ""),
                )
                asyncio.create_task(self._relay_finding(self._session, payload))
        except Exception:
            logger.exception("failed to parse data_received payload")

    def _on_fact(self, incident: IncidentState) -> None:
        """Synchronous dispatch decisions — no awaits allowed here."""
        facts = incident.facts
        logger.debug("supervisor FACT update: facts=%s", facts)

        has_location = "location" in facts
        has_chemical = "un_number" in facts or "chemical" in facts

        # Dispatch web research once we have location + chemical identifier.
        if has_location and has_chemical and "web_research" not in self._dispatched:
            self._dispatched.add("web_research")
            logger.info(
                "dispatching web_research: location=%s un_number=%s",
                facts.get("location"),
                facts.get("un_number"),
            )
            asyncio.create_task(web_research(incident))

        # Dispatch agency liaisons once we have location + chemical identifier.
        if has_location and has_chemical:
            asyncio.create_task(self._dispatch_agencies(incident))

    async def _dispatch_agencies(self, incident: IncidentState) -> None:
        if self._ctx is None:
            return
        for agency in seed_data.agencies():
            key = f"agency_{agency['id']}"
            if key in self._dispatched:
                continue
            if _should_dispatch_agency(agency, incident.facts):
                self._dispatched.add(key)
                logger.info("dispatching liaison for %s", agency["id"])
                try:
                    await connect_party(self._ctx, incident, agency, mode=agency["mode"])
                except Exception:
                    logger.exception("failed to dispatch liaison for %s", agency["id"])

    async def _relay_finding(self, session: AgentSession, payload: dict) -> None:
        source = payload.get("source", "worker")
        summary = payload.get("summary", "")
        logger.info("relaying finding from %s: %.80s", source, summary)
        await session.generate_reply(
            instructions=(
                f"A live field update just came in from {source}: {summary} "
                "Relay this to the caller in one calm sentence, then ask if they have any updates."
            )
        )
