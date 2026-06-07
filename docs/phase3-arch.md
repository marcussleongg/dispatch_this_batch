# Phase 3 Architecture: Agency Dispatch

## What Phase 3 adds

When the supervisor collects `location` + `un_number`/`chemical`, it dynamically
dispatches voice agents into sub-rooms that simulate parallel agency calls (Fire/HazMat,
Poison Control). Each sub-call produces a finding that is relayed back to the caller by
the orchestrator — the same relay path already used by web_research.

---

## Component roles

| Component | Runs as | Writes to | Reads from |
|---|---|---|---|
| **CallTaker** | `Agent` in main room | `incident.facts` | nothing (tools do the writing) |
| **Supervisor** | `asyncio.Task`, event-driven loop | dispatches tasks | `incident.events` queue |
| **Web Research** | `asyncio.create_task`, no room | `incident.findings` | `incident.facts` (snapshot at start) |
| **Liaison** | dispatched `Agent` in sub-room | `incident.findings` + emits FINDING | `incident.facts` (snapshot at brief time) |
| **Agency Sim** | dispatched `Agent` in same sub-room | nothing | liaison's spoken words only |

---

## Coordination model

Everything coordinates through one object and one queue:

```
incident.facts  ←──── CallTaker.record_fact()
                          │
                          └──► incident.events (asyncio.Queue)
                                     │
                              Supervisor (sole consumer)
                              ├── web_research(incident)      asyncio.create_task
                              └── connect_party(ctx, agency)  await dispatch
                                        │
                              Liaison joins sub-room
                              ├── dispatches Agency Sim → same sub-room
                              └── submit_finding(summary)
                                      │
                                      ▼
                              incident.findings + FINDING event
                                      │
                              Supervisor._relay_finding()
                                      │
                              session.generate_reply() → caller hears result
```

**Supervisor is the only coordinator.** No agent polls for updates. No agent speaks
to another agent except through the event queue (liaison → finding → supervisor → caller).

---

## Data freshness model

**No polling anywhere.** The only trigger is `incident.events.get()`.

- `IncidentState` is a single Python object. Any component with a reference sees
  mutations immediately (single-threaded asyncio, no copies).
- The supervisor fires on each FACT/FINDING event — it always has the latest state
  at the moment it runs.
- Workers (web_research, liaison) read `incident.facts` once at startup. This is a
  snapshot, intentionally: we only dispatch after critical facts are confirmed, so the
  snapshot will have `location` + chemical identifier. Later facts are relayed directly
  by the supervisor to the caller, independent of already-running workers.
- Agency sim has zero access to `IncidentState` — it's stateless, purely reactive to
  the liaison's spoken words.

---

## Termination model

| Component | How it ends | Safety net |
|---|---|---|
| **Liaison** | `submit_finding()` sets `done_event` → `run_liaison()` exits `wait_for` → `session.aclose()` | 90s timeout |
| **Agency Sim** | Liaison leaves room → LiveKit closes empty room → `wait_for_disconnect()` returns | 120s timeout |
| **Web Research** | One-shot coroutine — returns after emitting finding | — |
| **Supervisor** | Runs for the life of the call (cancelled when orchestrator session ends) | — |

The 90 / 120 second timeouts are safety nets for demo reliability — a stuck sub-call
won't hang the process.

---

## Single-handler routing (AgentServer constraint)

`AgentServer` supports only **one** `@server.rtc_session()` registration. All agent
types are dispatched with `agent_name="dispatch-orchestrator"` (the one registered
name) and a `role` key in JSON metadata. The single handler routes internally:

```python
@server.rtc_session(agent_name="dispatch-orchestrator")
async def session_handler(ctx: JobContext) -> None:
    meta = json.loads(ctx.job.metadata or "{}")
    role = meta.get("role", "orchestrator")
    if role == "liaison":
        await run_liaison(ctx, meta)
    elif role == "agency_sim":
        await run_agency_sim(ctx, meta)
    else:
        await _run_orchestrator(ctx)
```

Sub-rooms are named `{incident_id}-{agency_id}` (e.g. `abc123-fire_hazmat`).
Supervisor passes the sub-room name in the dispatch request; liaison dispatches the
agency-sim into the same room using `ctx.room.name`.

---

## Incident registry

Since liaison and agency-sim jobs run as separate dispatched workers, they don't
receive `IncidentState` directly. A module-level dict in `state.py` bridges the gap:

```python
# state.py
_registry: dict[str, IncidentState] = {}

def register_incident(incident: IncidentState) -> None:
    _registry[incident.incident_id] = incident

def get_incident(incident_id: str) -> IncidentState | None:
    return _registry.get(incident_id)
```

`register_incident()` is called early in `_run_orchestrator()`. Liaison calls
`get_incident(meta["incident_id"])` to get the live object. Since it's the same
Python object (not a copy), all facts added after registration are immediately visible.

---

## Agency dispatch triggering logic

Supervisor dispatches agencies once `location + (un_number | chemical)` are confirmed.
Matching is keyword-based against each agency's `handles` list (from `agencies.json`):

- **Fire/HazMat** — dispatched whenever `un_number` or `chemical` is present (always
  relevant for hazmat)
- **Poison Control** — dispatched additionally when `injuries` is present
- **Public Works** — skipped for the hazmat demo scenario

Cap: max 2 agencies for a legible dashboard + tight latency.

---

## Files created / modified

| File | Action | Purpose |
|---|---|---|
| `agents/state.py` | modify | add `register_incident` / `get_incident` |
| `agents/worker.py` | modify | single-handler routing + pass `ctx` to Supervisor |
| `agents/supervisor.py` | modify | accept `ctx`, add `_dispatch_agencies()` |
| `agents/connect.py` | implement | `connect_party(ctx, incident, agency, mode)` |
| `agents/liaison.py` | implement | `LiaisonAgent` + `run_liaison()` |
| `agents/agency_sim.py` | implement | `AgencySimAgent` + `run_agency_sim()` |
| `agents/voices.py` | implement | per-role Cartesia voice registry |
