"""Supervisor — LLM-driven dispatch coordinator (same process as call-taker).

Subscribes to the IncidentState event bus. On each significant event (FACT,
RESEARCH_TRIGGER) it calls an LLM with the current incident state and a
dispatch() function tool. The LLM decides which resources to send; the
supervisor executes those decisions. All hardcoded condition checks are gone.

Dashboard packets (topic="dashboard") are also published here so the browser
canvas updates in real-time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from livekit.agents import AgentSession, function_tool
from livekit.agents import llm as lk_llm

import seed_data
from connect import connect_party
from llm import build_llm
from state import EventType, IncidentState
from web_research import web_research

logger = logging.getLogger("supervisor")

# Human-readable labels for each worker_id used in dashboard tiles.
_LABELS: dict[str, str] = {
    "orchestrator":           "Call-Taker",
    "supervisor":             "Supervisor",
    "web_research":           "Web Research",
    "agency_fire_hazmat":     "Fire / HazMat Response",
    "agency_poison_control":  "Poison Control",
    "agency_public_works":    "Public Works / Highway",
    "sim_fire_hazmat":        "Fire / HazMat (agency)",
    "sim_poison_control":     "Poison Control (agency)",
    "sim_public_works":       "Public Works (agency)",
}


def _worker_label(worker_id: str) -> str:
    return _LABELS.get(worker_id, worker_id)


def _source_to_worker_id(source: str) -> str:
    if source.startswith("agency_") or source.startswith("sim_"):
        return source
    return f"agency_{source}"


# ── Supervisor LLM tool + prompt ───────────────────────────────────────────────

@function_tool
def _supervisor_dispatch(actions: list[str]) -> str:
    """Dispatch one or more resources for this incident.

    Args:
        actions: list of action identifiers to dispatch. Valid values:
            "web_research"          — live web search for wind/weather/conditions
            "agency_fire_hazmat"    — Fire / HazMat response team
            "agency_poison_control" — Poison Control (use when injuries/symptoms present)
            "agency_public_works"   — Public Works / Highway (road closure, traffic)
    """
    return "ok"


SUPERVISOR_PROMPT = """\
You are the dispatch supervisor for a hazmat 911 emergency call center. Your job is
to decide which resources to send based on the current incident state. You work
silently — you never speak to the caller.

Dispatch early on partial information. Do not wait for all facts to be confirmed.

Guidelines:
- web_research: dispatch as soon as you have a location OR a chemical. Always useful
  for current wind, weather, and hazmat conditions data.
- agency_fire_hazmat: dispatch when any hazardous material (spill, leak, gas, chemical,
  UN number) is confirmed.
- agency_poison_control: dispatch when a chemical AND injuries or exposure symptoms
  are both confirmed.
- agency_public_works: dispatch when the incident is on a public road and may require
  traffic management or road closure.

You will be told what is already dispatched — do not repeat those.
If nothing new should be dispatched, call dispatch with an empty list.
"""


def _build_state_message(incident: IncidentState, dispatched: set[str], hint: str) -> str:
    facts = "\n".join(f"  {k}: {v}" for k, v in incident.facts.items()) or "  (none yet)"
    already = ", ".join(sorted(dispatched)) or "none"
    hint_line = f"\ncall_taker_hint: {hint}" if hint else ""
    return f"facts:\n{facts}\nalready_dispatched: {already}{hint_line}"


# ── Supervisor class ───────────────────────────────────────────────────────────

class Supervisor:
    def __init__(self) -> None:
        self._dispatched: set[str] = set()
        self._llm = build_llm("supervisor")
        self._reason_pending: bool = False
        self._pending_hint: str = ""
        self._ctx = None
        self._session: AgentSession | None = None
        self._incident: IncidentState | None = None
        self._tasks: set[asyncio.Task] = set()  # strong refs so GC doesn't eat them

    def _create_task(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    async def run(self, incident: IncidentState, session: AgentSession, ctx) -> None:
        self._ctx = ctx
        self._session = session
        self._incident = incident
        logger.info("Supervisor started for incident %s", incident.incident_id)

        ctx.room.on("data_received", self._on_data_received)
        ctx.room.on("participant_connected", self._on_participant_active)
        ctx.room.on("participant_active", self._on_participant_active)

        self._create_task(self._periodic_snapshot())

        try:
            while True:
                event = await incident.events.get()
                if event.type == EventType.FACT:
                    self._on_fact(incident, event.payload)
                elif event.type == EventType.RESEARCH_TRIGGER:
                    self._on_research_trigger(incident, event.payload)
                elif event.type == EventType.FINDING:
                    self._create_task(self._relay_finding(session, event.payload))
        finally:
            if incident.live is not None:
                await incident.live.destroy()

    # ── LLM reasoning ─────────────────────────────────────────────────────────

    def _schedule_reason(self, incident: IncidentState, hint: str = "") -> None:
        """Schedule a debounced LLM reasoning call. Coalesces rapid FACT bursts."""
        if hint:
            self._pending_hint = hint
        if not self._reason_pending:
            self._reason_pending = True
            self._create_task(self._debounced_reason(incident))

    async def _debounced_reason(self, incident: IncidentState) -> None:
        await asyncio.sleep(0.3)
        hint, self._pending_hint = self._pending_hint, ""
        self._reason_pending = False
        await self._reason(incident, hint=hint)

    async def _reason(self, incident: IncidentState, hint: str = "") -> None:
        """Call the supervisor LLM to decide what to dispatch."""
        state_msg = _build_state_message(incident, self._dispatched, hint)
        logger.info("_reason: calling LLM  facts=%s dispatched=%s hint=%r",
                    list(incident.facts.keys()), sorted(self._dispatched), hint)

        ctx = lk_llm.ChatContext()
        ctx.add_message(role="system", content=SUPERVISOR_PROMPT)
        ctx.add_message(role="user", content=state_msg)

        try:
            response = await self._llm.chat(
                chat_ctx=ctx,
                tools=[_supervisor_dispatch],
                tool_choice="required",
            ).collect()
        except Exception:
            logger.exception("supervisor LLM call failed; skipping dispatch reasoning")
            return

        logger.info("_reason: LLM returned tool_calls=%d text=%r",
                    len(response.tool_calls),
                    (response.text or "")[:120])

        for tc in response.tool_calls:
            logger.info("_reason: tool_call name=%r args=%r", tc.name, tc.arguments)
            if tc.name not in ("_supervisor_dispatch", "dispatch_resources"):
                logger.warning("_reason: unexpected tool name %r — skipping", tc.name)
                continue
            raw = tc.arguments
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except Exception:
                    logger.warning("_reason: could not parse arguments JSON: %r", raw)
                    continue
            actions = raw.get("actions", [])
            if not isinstance(actions, list):
                logger.warning("supervisor LLM returned non-list actions: %r", actions)
                return
            logger.info("supervisor LLM decided: %s  hint=%r", actions, hint)
            for action in actions:
                await self._execute_action(action, incident)

    async def _execute_action(self, action: str, incident: IncidentState) -> None:
        """Execute a single dispatch action returned by the LLM."""
        if action in self._dispatched:
            logger.debug("action %r already dispatched, skipping", action)
            return

        if action == "web_research":
            self._dispatched.add(action)
            logger.info("dispatching web_research")
            incident.workers["web_research"] = "spawning"
            self._create_task(self._publish_worker_status(incident, "web_research", "spawning"))
            self._create_task(self._run_web_research(incident))

        elif action.startswith("agency_"):
            agency_id = action.removeprefix("agency_")
            agency = next((a for a in seed_data.agencies() if a["id"] == agency_id), None)
            if agency is None:
                logger.warning("supervisor LLM returned unknown agency: %r", action)
                return
            self._dispatched.add(action)
            logger.info("dispatching liaison for %s", agency_id)
            self._create_task(self._dispatch_single_agency(incident, agency))

        else:
            logger.warning("supervisor LLM returned unknown action: %r", action)

    # ── Fact / trigger handling ────────────────────────────────────────────────

    def _on_fact(self, incident: IncidentState, fact_payload: dict) -> None:
        key = fact_payload.get("key", "")
        value = fact_payload.get("value", "")
        logger.debug("supervisor FACT: %s=%r  facts=%s", key, value, incident.facts)

        # Immediately fire web research as soon as location is known — don't wait for LLM.
        if key == "location" and "web_research" not in self._dispatched:
            self._dispatched.add("web_research")
            logger.info("location known — immediately dispatching web_research")
            incident.workers["web_research"] = "spawning"
            self._create_task(self._publish_worker_status(incident, "web_research", "spawning"))
            self._create_task(self._run_web_research(incident))

        self._schedule_reason(incident)
        self._create_task(self._publish({
            "type": "fact_update",
            "ts": time.time(),
            "incident_id": incident.incident_id,
            "key": key,
            "value": value,
        }))

    def _on_research_trigger(self, incident: IncidentState, payload: dict) -> None:
        reason = payload.get("reason", "")
        logger.info("supervisor RESEARCH_TRIGGER: reason=%r", reason)

        # Immediately fire web research — the call-taker explicitly signalled a need.
        if "web_research" not in self._dispatched:
            self._dispatched.add("web_research")
            logger.info("RESEARCH_TRIGGER — immediately dispatching web_research reason=%r", reason)
            incident.workers["web_research"] = "spawning"
            self._create_task(self._publish_worker_status(incident, "web_research", "spawning"))
            self._create_task(self._run_web_research(incident, reason=reason))

        self._schedule_reason(incident, hint=reason)

    async def _run_web_research(self, incident: IncidentState, reason: str = "") -> None:
        await web_research(incident, reason=reason)

    # ── Agency dispatch ────────────────────────────────────────────────────────

    async def _dispatch_single_agency(self, incident: IncidentState, agency: dict) -> None:
        if self._ctx is None:
            return
        key = f"agency_{agency['id']}"
        sub_room = f"{incident.incident_id}-{agency['id']}"
        incident.workers[key] = "spawning"
        self._create_task(self._publish_worker_status(
            incident, key, "spawning", label=agency["name"], sub_room=sub_room
        ))
        try:
            await connect_party(self._ctx, incident, agency, mode=agency["mode"])
            incident.workers[key] = "active"
            self._create_task(self._publish_worker_status(
                incident, key, "active", label=agency["name"], sub_room=sub_room
            ))
            if incident.live is not None:
                note = f"{agency['name']} has been contacted and is responding to this incident."
                self._create_task(incident.live.add(note, source="supervisor"))
        except Exception:
            logger.exception("failed to dispatch liaison for %s", agency["id"])
            incident.workers[key] = "error"
            self._create_task(self._publish_worker_status(
                incident, key, "error", label=agency["name"], sub_room=sub_room
            ))

    # ── Dashboard publishing ───────────────────────────────────────────────────

    async def _publish_worker_status(
        self,
        incident: IncidentState,
        worker_id: str,
        status: str,
        *,
        label: str | None = None,
        sub_room: str | None = None,
    ) -> None:
        await self._publish({
            "type": "worker_status",
            "ts": time.time(),
            "incident_id": incident.incident_id,
            "worker_id": worker_id,
            "label": label or _worker_label(worker_id),
            "status": status,
            "sub_room": sub_room,
            "parent_id": "supervisor",
        })

    async def _publish(self, payload: dict) -> None:
        if self._ctx is None:
            logger.warning("dashboard publish skipped: ctx is None (type=%s)", payload.get("type"))
            return
        ptype = payload.get("type")
        try:
            data = json.dumps(payload).encode()
            await self._ctx.room.local_participant.publish_data(
                data, reliable=True, topic="dashboard",
            )
            logger.info("dashboard published ok: type=%s bytes=%d", ptype, len(data))
        except Exception:
            logger.exception("dashboard publish FAILED: type=%s", ptype)

    def _on_participant_active(self, participant) -> None:
        logger.debug("participant event: identity=%s", participant.identity)
        if participant.identity.startswith("dashboard-"):
            logger.info("dashboard viewer joined: %s — sending snapshot", participant.identity)
            self._create_task(self._send_snapshot())

    async def _periodic_snapshot(self) -> None:
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
            "orchestrator": {"label": "Call-Taker", "status": "active", "sub_room": None},
            "supervisor":   {"label": "Supervisor",  "status": "active", "sub_room": None},
        }
        for wid, status in inc.workers.items():
            if wid.startswith("agency_"):
                sub_room = f"{inc.incident_id}-{wid.removeprefix('agency_')}"
            elif wid.startswith("sim_"):
                sub_room = f"{inc.incident_id}-{wid.removeprefix('sim_')}"
            else:
                sub_room = None
            workers[wid] = {"label": _worker_label(wid), "status": status, "sub_room": sub_room}
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
                self._create_task(self._relay_finding(self._session, payload))
        except Exception:
            logger.exception("failed to parse data_received payload")

    # ── Finding relay ──────────────────────────────────────────────────────────

    async def _relay_finding(self, session: AgentSession, payload: dict) -> None:
        source = payload.get("source", "worker")
        summary = payload.get("summary", "")
        logger.info("relaying finding from %s: %.80s", source, summary)

        worker_id = _source_to_worker_id(source)

        self._create_task(self._publish({
            "type": "finding",
            "ts": time.time(),
            "incident_id": self._incident.incident_id if self._incident else "unknown",
            "source": source,
            "source_label": _worker_label(worker_id),
            "summary": summary,
        }))

        if self._incident and worker_id in self._incident.workers:
            self._incident.workers[worker_id] = "done"
            sub_room = None
            if worker_id.startswith("agency_"):
                sub_room = f"{self._incident.incident_id}-{worker_id.removeprefix('agency_')}"
            self._create_task(self._publish_worker_status(
                self._incident, worker_id, "done", sub_room=sub_room
            ))

        # Only index agency findings here — web_research already indexes itself in _record.
        if self._incident and self._incident.live is not None and source != "web_research":
            self._create_task(self._incident.live.add(summary, source=source))

        await session.say(
            f"Update from {source}: {summary}",
            allow_interruptions=True,
        )
