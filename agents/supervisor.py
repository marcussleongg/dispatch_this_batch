"""Phase 2+3: Supervisor — event-driven asyncio control loop (same process).

Subscribes to the IncidentState event bus and OWNS ALL dispatch of action agents.
The loop only ever awaits the queue — dispatch decisions are synchronous and relay
actions are fire-and-forget tasks, so new FACT events are never blocked by an
in-progress relay.

Phase 4: also publishes dashboard packets (topic="dashboard") to the main LiveKit
room so the browser canvas updates in real-time. All publishing is centralised here;
no other agent file needs to touch send_data for dashboard purposes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from livekit.agents import AgentSession

import seed_data
from connect import connect_party
from state import EventType, IncidentState
from web_research import web_research

logger = logging.getLogger("supervisor")

# Human-readable labels for each worker_id used in dashboard tiles.
_LABELS: dict[str, str] = {
    "orchestrator":        "Call-Taker",
    "supervisor":          "Supervisor",
    "web_research":        "Web Research",
    "agency_fire_hazmat":  "Fire / HazMat Response",
    "agency_poison_control": "Poison Control",
    "agency_public_works": "Public Works / Highway",
    "sim_fire_hazmat":     "Fire / HazMat (agency)",
    "sim_poison_control":  "Poison Control (agency)",
    "sim_public_works":    "Public Works (agency)",
}


def _worker_label(worker_id: str) -> str:
    return _LABELS.get(worker_id, worker_id)


def _source_to_worker_id(source: str) -> str:
    """Convert a finding source string to a worker_id key."""
    if source.startswith("agency_") or source.startswith("sim_"):
        return source
    return f"agency_{source}"


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
        self._incident: IncidentState | None = None

    async def run(self, incident: IncidentState, session: AgentSession, ctx) -> None:
        self._ctx = ctx
        self._session = session
        self._incident = incident
        logger.info("Supervisor started for incident %s", incident.incident_id)

        ctx.room.on("data_received", self._on_data_received)
        ctx.room.on("participant_connected", self._on_participant_active)
        ctx.room.on("participant_active", self._on_participant_active)

        asyncio.create_task(self._periodic_snapshot())

        while True:
            event = await incident.events.get()
            if event.type == EventType.FACT:
                self._on_fact(incident, event.payload)
            elif event.type == EventType.FINDING:
                asyncio.create_task(
                    self._relay_finding(session, event.payload)
                )

    # ── Dashboard publishing ───────────────────────────────────────────────────

    async def _publish(self, payload: dict) -> None:
        """Publish a dashboard packet to all room participants via the agent's participant data channel."""
        if self._ctx is None:
            logger.warning("dashboard publish skipped: ctx is None (type=%s)", payload.get("type"))
            return
        ptype = payload.get("type")
        try:
            data = json.dumps(payload).encode()
            await self._ctx.room.local_participant.publish_data(
                data,
                reliable=True,
                topic="dashboard",
            )
            logger.info("dashboard published ok: type=%s bytes=%d", ptype, len(data))
        except Exception:
            logger.exception("dashboard publish FAILED: type=%s", ptype)

    def _on_participant_active(self, participant) -> None:
        """Send a full state snapshot when a dashboard viewer joins or becomes active."""
        logger.debug("participant event: identity=%s", participant.identity)
        if participant.identity.startswith("dashboard-"):
            logger.info("dashboard viewer joined: %s — sending snapshot", participant.identity)
            asyncio.create_task(self._send_snapshot())

    async def _periodic_snapshot(self) -> None:
        """Broadcast a snapshot every 3 s so dashboards always catch up."""
        tick = 0
        while True:
            await asyncio.sleep(3)
            tick += 1
            logger.info("periodic snapshot tick=%d", tick)
            await self._send_snapshot()

    async def _send_snapshot(self) -> None:
        inc = self._incident
        if inc is None:
            return
        workers: dict = {
            "orchestrator": {"label": "Call-Taker",  "status": "active", "sub_room": None},
            "supervisor":   {"label": "Supervisor",  "status": "active", "sub_room": None},
        }
        for wid, status in inc.workers.items():
            if wid.startswith("agency_"):
                agency_id = wid.removeprefix("agency_")
                sub_room = f"{inc.incident_id}-{agency_id}"
            elif wid.startswith("sim_"):
                sim_id = wid.removeprefix("sim_")
                sub_room = f"{inc.incident_id}-{sim_id}"
            else:
                sub_room = None
            workers[wid] = {
                "label": _worker_label(wid),
                "status": status,
                "sub_room": sub_room,
            }
        await self._publish({
            "type": "snapshot",
            "ts": time.time(),
            "incident_id": inc.incident_id,
            "facts": dict(inc.facts),
            "workers": workers,
            "findings": list(inc.findings),
        })

    # ── LiveKit data_received (findings from liaisons in sub-rooms) ────────────

    def _on_data_received(self, data_packet) -> None:
        """Handle findings published by liaison agents via LiveKit data messages."""
        # Liaisons send findings via the server-side send_data API (topic="finding").
        # Only process those; ignore anything else on the data channel.
        if getattr(data_packet, "topic", None) != "finding":
            return
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

    # ── Fact handling ──────────────────────────────────────────────────────────

    def _on_fact(self, incident: IncidentState, fact_payload: dict) -> None:
        """Synchronous dispatch decisions — no awaits allowed here."""
        facts = incident.facts
        key = fact_payload.get("key", "")
        value = fact_payload.get("value", "")
        logger.debug("supervisor FACT update: facts=%s", facts)

        has_location = "location" in facts
        has_chemical = "un_number" in facts or "chemical" in facts

        if has_location and has_chemical and "web_research" not in self._dispatched:
            self._dispatched.add("web_research")
            logger.info(
                "dispatching web_research: location=%s un_number=%s",
                facts.get("location"),
                facts.get("un_number"),
            )
            incident.workers["web_research"] = "spawning"
            asyncio.create_task(self._publish({
                "type": "worker_status",
                "ts": time.time(),
                "incident_id": incident.incident_id,
                "worker_id": "web_research",
                "label": "Web Research",
                "status": "spawning",
                "sub_room": None,
                "parent_id": "supervisor",
            }))
            asyncio.create_task(self._run_web_research(incident))

        if has_location and has_chemical:
            asyncio.create_task(self._dispatch_agencies(incident))

        # Publish the confirmed fact to the dashboard.
        asyncio.create_task(self._publish({
            "type": "fact_update",
            "ts": time.time(),
            "incident_id": incident.incident_id,
            "key": key,
            "value": value,
        }))

    async def _run_web_research(self, incident: IncidentState) -> None:
        """Run web research; the FINDING event it emits triggers _relay_finding."""
        await web_research(incident)

    # ── Agency dispatch ────────────────────────────────────────────────────────

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
                sub_room = f"{incident.incident_id}-{agency['id']}"
                incident.workers[key] = "spawning"
                asyncio.create_task(self._publish({
                    "type": "worker_status",
                    "ts": time.time(),
                    "incident_id": incident.incident_id,
                    "worker_id": key,
                    "label": agency["name"],
                    "status": "spawning",
                    "sub_room": sub_room,
                    "parent_id": "supervisor",
                }))
                try:
                    await connect_party(self._ctx, incident, agency, mode=agency["mode"])
                    incident.workers[key] = "active"
                    asyncio.create_task(self._publish({
                        "type": "worker_status",
                        "ts": time.time(),
                        "incident_id": incident.incident_id,
                        "worker_id": key,
                        "label": agency["name"],
                        "status": "active",
                        "sub_room": sub_room,
                        "parent_id": "supervisor",
                    }))
                except Exception:
                    logger.exception("failed to dispatch liaison for %s", agency["id"])
                    incident.workers[key] = "error"
                    asyncio.create_task(self._publish({
                        "type": "worker_status",
                        "ts": time.time(),
                        "incident_id": incident.incident_id,
                        "worker_id": key,
                        "label": agency["name"],
                        "status": "error",
                        "sub_room": sub_room,
                        "parent_id": "supervisor",
                    }))

    # ── Finding relay ──────────────────────────────────────────────────────────

    async def _relay_finding(self, session: AgentSession, payload: dict) -> None:
        source = payload.get("source", "worker")
        summary = payload.get("summary", "")
        logger.info("relaying finding from %s: %.80s", source, summary)

        worker_id = _source_to_worker_id(source)

        # Publish finding to dashboard.
        asyncio.create_task(self._publish({
            "type": "finding",
            "ts": time.time(),
            "incident_id": self._incident.incident_id if self._incident else "unknown",
            "source": source,
            "source_label": _worker_label(worker_id),
            "summary": summary,
        }))

        # Mark the worker done.
        if self._incident and worker_id in self._incident.workers:
            self._incident.workers[worker_id] = "done"
            sub_room = None
            if worker_id.startswith("agency_"):
                sub_room = f"{self._incident.incident_id}-{worker_id.removeprefix('agency_')}"
            asyncio.create_task(self._publish({
                "type": "worker_status",
                "ts": time.time(),
                "incident_id": self._incident.incident_id,
                "worker_id": worker_id,
                "label": _worker_label(worker_id),
                "status": "done",
                "sub_room": sub_room,
                "parent_id": "supervisor",
            }))

        await session.generate_reply(
            instructions=(
                f"A live field update just came in from {source}: {summary} "
                "Relay this to the caller in one calm sentence, then ask if they have any updates."
            )
        )
