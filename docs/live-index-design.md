# Live Conditions Index — Design

## What it is

A per-call Moss index (`live-{incident_id}`) that accumulates findings and annotations
during an active incident and makes them queryable by any agent in the system.

Three sources write to it:

| Source | Content |
|--------|---------|
| `web_research` | Tavily wind, weather, road conditions |
| `agency_{id}` | Guidance relayed back from a liaison sub-call |
| `supervisor` | Dispatch annotations (which agencies were contacted) |

The call-taker queries it via the `lookup_conditions` function tool to recall live
findings mid-call (e.g. "what was the wind direction again?").

---

## Why we use both a local session and a cloud index

We use a Moss `SessionIndex` (local, in-memory) combined with `push_index()` (cloud
sync) on every write. This is a deliberate write-through design driven by a hard
constraint: **LiveKit dispatches each agent role into a separate OS process**, and
separate processes cannot share in-memory state.

### The LiveKit process model

When the supervisor dispatches a liaison via `create_dispatch()`, LiveKit picks a
worker from its process pool and hands it the job. That worker runs `worker.py`
fresh — a completely isolated OS process. The same happens when the liaison
dispatches the agency sim into its sub-room. For a single incident with two agencies,
there are at minimum five separate OS processes:

```
Orchestrator process          (call-taker + supervisor)
├── Liaison process           (dispatched by supervisor for agency A)
│   └── Agency sim process    (dispatched by liaison into sub-room A)
└── Liaison process           (dispatched by supervisor for agency B)
    └── Agency sim process    (dispatched by liaison into sub-room B)
```

None of these share memory. Information crosses process boundaries only through:
- **Dispatch metadata** (JSON passed at dispatch time — facts snapshot)
- **LiveKit data channel** (`send_data`, topic=`"finding"`) — findings from sub-rooms back to supervisor
- **Cloud storage** — the only shared read/write store any process can reach by name

### Why local session at all?

The call-taker (in the orchestrator process) queries live conditions frequently and
needs low latency. A pure cloud-index approach would add a network round trip to
every `lookup_conditions` call. A `SessionIndex` keeps an in-memory replica so the
call-taker's queries run locally at ~1–10 ms with no cloud round trip.

### Why push to cloud at all?

So that liaison agents — running in separate OS processes — can query the same index
by name (`live-{incident_id}`, which is already in their dispatch metadata). Without
the cloud push, the in-memory session is invisible to any other process.

### Combined: write-through

```
add(text, source)
  → session.add_docs()     ← in-memory, immediately queryable by call-taker
  → session.push_index()   ← cloud sync, queryable by liaisons in other processes

lookup(query)              ← always hits the local session (~1–10 ms)
```

---

## Lifecycle

```
Call starts
  → LiveConditions.__init__()
  → client.session("live-{incident_id}")   # synchronous, no network call
  → empty in-memory session ready

During call (web research, agency findings, supervisor annotations)
  → live.add(text, source)
  → session.add_docs()  +  session.push_index()

Call-taker queries mid-call
  → live.lookup(query)
  → session.query()  [local, ~1–10 ms]

Liaison queries (separate process, in the future)
  → MossClient.query("live-{incident_id}", query)  [cloud]
  → index_name derived from incident_id already in dispatch metadata

Call ends
  → supervisor finally block → live.destroy()
  → client.delete_index("live-{incident_id}")  [removes cloud index]
  → in-memory session discarded with the process
```

---

## What is not shared via this index

Facts confirmed by the caller (`location`, `un_number`, etc.) travel via
`record_fact` → `IncidentState.facts` (in-process) and are snapshotted into
dispatch metadata at liaison launch time. They do not go through the live index.

Agency findings travel back via LiveKit `send_data` (topic=`"finding"`) from the
liaison's sub-room to the supervisor's `data_received` handler, which then writes
them to the live index so the call-taker can recall them.
