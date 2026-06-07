"""Phase 2: Supervisor — event-driven asyncio control loop (same process).

Subscribes to the IncidentState event bus and OWNS ALL dispatch of action agents.
The loop only ever awaits the queue — dispatch decisions are synchronous and relay
actions are fire-and-forget tasks, so new FACT events are never blocked by an
in-progress relay.
"""

from __future__ import annotations

import asyncio
import logging

from livekit.agents import AgentSession

from state import EventType, IncidentState
from web_research import web_research

logger = logging.getLogger("supervisor")


class Supervisor:
    def __init__(self) -> None:
        self._dispatched: set[str] = set()

    async def run(self, incident: IncidentState, session: AgentSession) -> None:
        logger.info("Supervisor started for incident %s", incident.incident_id)
        while True:
            event = await incident.events.get()   # only await in the loop
            if event.type == EventType.FACT:
                self._on_fact(incident)            # synchronous — never blocks
            elif event.type == EventType.FINDING:
                asyncio.create_task(               # fire and forget
                    self._relay_finding(session, event.payload)
                )

    def _on_fact(self, incident: IncidentState) -> None:
        """Synchronous dispatch decisions — no awaits allowed here."""
        facts = incident.facts
        logger.debug("supervisor FACT update: facts=%s", facts)

        # Dispatch web research once we have location + chemical identifier.
        if (
            "location" in facts
            and ("un_number" in facts or "chemical" in facts)
            and "web_research" not in self._dispatched
        ):
            self._dispatched.add("web_research")
            logger.info(
                "dispatching web_research: location=%s un_number=%s",
                facts.get("location"),
                facts.get("un_number"),
            )
            asyncio.create_task(web_research(incident))

    async def _relay_finding(self, session: AgentSession, payload: dict) -> None:
        source = payload.get("source", "worker")
        summary = payload.get("summary", "")
        logger.info("relaying finding from %s: %.80s", source, summary)
        await session.generate_reply(
            instructions=(
                f"A live field update just came in: {summary} "
                "Relay this to the caller in one calm sentence, then ask if they have any updates."
            )
        )
