"""Phase 2: Supervisor — event-driven asyncio control loop (same process).

Subscribes to the IncidentState event bus (facts, findings, timers) and OWNS ALL
dispatch of action agents. Brain = a single LLM planner (constrained tool-calls
over a small fixed action set) via TrueFoundry. Spawns/escalates off the caller's
clock, not just on caller turns.

TODO(Phase 2): `await incident.events`; call planner; execute returned tool-calls
(web research = asyncio.create_task; voice workers = createDispatch).
"""
