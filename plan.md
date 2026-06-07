# Plan: AI Emergency Dispatch Swarm (MossHack)

## Context

Hackathon build (Moss Conversational AI Hackathon @ YC).

An **AI emergency-dispatch call center**. A single inbound 911-style call is answered
by an AI call-taker; a supervisor then **dynamically spins up a swarm of helper agents** mid-call —
searches the web for live conditions, "calls" other government agencies (poison control, fire/hazmat)
— in parallel, far faster than a lone human dispatcher. A live **command-center dashboard** shows each
agent/call spawning with streaming transcripts and results.

**One entry point, single voice.** Field officers and 911 callers call the _same_ orchestrator, and the
**orchestrator is the only voice to the caller** (no specialist agent speaks to the caller — avoids
turn-taking/overlap).

**Retrieval stays inline (Moss in the hot path).** The guidebook is **a tool, not a separate agent** —
the orchestrator calls Moss directly (`lookup_guidebook`) and reasons over the result in its own LLM.
Moss's whole value prop is that retrieval is fast enough to live in the conversational hot path
("retrieval is no longer the bottleneck" — the hackathon thesis), so isolating retrieval into its own
agent _for latency reasons is self-defeating_. Inline is simpler, lowest-latency, and the cleanest Moss
showcase: quoting a 400-page manual mid-sentence with no perceptible lag. Keep retrieved snippets tight
so they don't bloat the orchestrator's context (the only real downside of inlining; monitor on long calls).

**Separate agents are reserved for concurrent _actions_, not retrieval.** Spinning up parallel agents
pays off only where the system does several independent things at once — **web research** + **agency
calls** running simultaneously while the call continues. That parallelism is the real differentiator vs.
a generic voice-RAG bot, and it has nothing to do with retrieval speed. Quick question → orchestrator
answers inline via Moss; full incident → fan out the **action** agents (web, agency) in parallel.

**Demo thesis for judges:** low-latency retrieval (Moss, inline) + dynamic dispatch of parallel action
agents (LiveKit) = one call becomes a coordinated multi-agency response in seconds. Dispatcher force-multiplier.

Decisions locked with user:

- **Separate supervisor (event-driven), because fan-out must run off the caller's clock.** The
  **orchestrator** stays voice-only (converse, extract facts, inline guidebook lookups, write facts/findings
  to the bus). The **supervisor** runs as a separate **event-driven async loop** (same worker process) that
  subscribes to the bus — new facts, worker findings, timers — and **owns all dispatch** of action agents, so
  it can spawn/escalate _between_ caller turns, not just on them.
- **Supervisor brain = a single LLM planner** (not a rules engine — simpler). De-risked with **constrained
  tool-calls over a small fixed action set** + low temp + rehearsal, plus an optional ~5-line hardcoded
  fallback for the make-or-break trigger. Loop = on event → call planner with incident state → execute
  the tool-calls it returns.
- **No external message bus.** Orchestrator + supervisor + workers run in **one process** and coordinate via
  in-process `asyncio` (a shared `IncidentState` + an `asyncio.Queue`). **Drops Redis and Postgres pub/sub.**
  Non-voice workers (web research) are just `asyncio.create_task` — still full tool-using agents, they just
  don't need a LiveKit room. Dashboard gets live updates over LiveKit data messages (or one WebSocket).
- **No database.** ERG document → **Moss** (semantic retrieval). Tiny structured config (agency directory,
  chemical fallback) → **seed JSON files** loaded at startup — _not_ Moss (fuzzy top-k where you need exact,
  complete config) and _not_ a DB (a handful of records). Live data → in-process `IncidentState`.
- **Single entry point + single voice:** no standalone guidebook line; orchestrator is the only voice to the caller.
- **Retrieval inline:** guidebook = a Moss **tool call** in the orchestrator, not a separate agent
  (Moss is fast enough for the hot path; simplest + best Moss demo).
- **Separate dispatched agents reserved for concurrent actions:** web research, agency calls — where
  multi-agent parallelism actually pays off.
- **Outbound agency calls:** **AI-to-AI simulated** agencies now, behind a `connect_party(mode=sim|sip)`
  abstraction so porting to a real **SIP/Twilio** trunk = flip a flag + add creds (no logic change).
- **Scenario:** emergency dispatch / **hazmat** (keeps ERG + guidebooks → reuses Unsiloed+Moss).
- **Voice stack:** **LiveKit Agents** — also the runtime that **dynamically spins up** voice agents/calls.
- **TrueFoundry = LLM gateway only.** It routes each LiveKit agent's LLM calls to **MiniMax** + **Qwen**
  (governance/observability). It **cannot** do the agent spin-up: TrueFoundry Agents are text/tool agents
  behind a gateway (no real-time voice, no per-call sub-agent spawning) — that stays LiveKit's job.
- **Unsiloed** parses PDFs → **Moss** indexes/retrieves.

No database needed — static data = JSON seed files, Moss = the ERG document, live state in-process.

## How dynamic agent spin-up works

Agent **types are predefined** (prompt + tools + voice, each registered with an `agentName`); **instances
are dispatched live**, driven by the conversation:

- `AgentDispatchService.createDispatch(agentName, room, metadata)` — spawn an agent into a room at
  runtime (room auto-created), passing per-job metadata (incident id, target, phone#). <150 ms dispatch.
- `ctx.api.sip.create_sip_participant(trunk, sip_call_to, room)` — place a real outbound phone call
  (the SIP-mode of `connect_party`).
- In-session **handoff** swaps the active agent; instances are built at runtime.
- **Not everything needs a room:** non-voice workers (e.g. web research) are just `asyncio.create_task` in
  one process — only **voice** participants (the agency call) need `createDispatch` / a LiveKit room.

**Spin-up is LiveKit's job, not TrueFoundry's.** TrueFoundry Agents are text/tool agents behind a
governance gateway — no real-time voice, no documented per-call sub-agent spawning. We use TrueFoundry
purely as the **LLM gateway** behind each LiveKit agent ([TF Agents docs](https://www.truefoundry.com/docs/ai-gateway/agents/truefoundry-agents)).

Refs: [Agent dispatch](https://docs.livekit.io/agents/build/dispatch/),
[Dispatch API](https://docs.livekit.io/reference/python/livekit/api/agent_dispatch_service.html),
[Outbound calls](https://docs.livekit.io/sip/outbound-calls/).

## Cast (dispatched agents vs inline tools)

| Component                                     | Role                                                                                                                                                                                                                                            | Implementation                                                                           |
| --------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| **Orchestrator / Call-Taker**                 | Voice loop (turn-driven): the **single voice** to the caller. Answers inbound, extracts incident facts, **calls `lookup_guidebook` (Moss) inline**, writes facts/findings to the bus, relays consolidated guidance back. **Does not dispatch.** | one voice agent; inline Moss tool; writes incident + emits events                        |
| **Supervisor**                                | Control loop (event-driven, same process): subscribes to the bus (facts, findings, timers) and **owns all dispatch** of action agents; monitors + escalates off the caller's clock                                                              | async loop; **LLM planner** (constrained tool-calls) via TrueFoundry; `dispatch_agent()` |
| **`lookup_guidebook`**                        | ERG protocol lookup over Moss — **a tool, not an agent**; result returned into orchestrator's own LLM                                                                                                                                           | `tools/guidebook.py` → Moss                                                              |
| **Web Research** _(in-process task)_          | Pulls live conditions (wind/weather, news, facility info) — no room needed                                                                                                                                                                      | `asyncio.create_task`; web search API                                                    |
| **Agency Liaison** _(dispatched voice agent)_ | "Calls" an agency, relays incident info, returns its response                                                                                                                                                                                   | LiveKit room; `connect_party(role, mode)`                                                |
| **Agency (sim)**                              | AI role-playing Poison Control / Fire-HazMat / Public Works on the other end of the liaison's call                                                                                                                                              | own prompt + own voice (heard on the sub-call/dashboard, not by the caller)              |

Assign MiniMax vs Qwen per role via TrueFoundry. The **caller only ever hears the orchestrator**; agency
sims have distinct voices on the liaison↔agency sub-call (surfaced on the dashboard) for demo flavor.

## Architecture

```
 Inbound caller (you) ─► LiveKit "main" room ─► Orchestrator/Call-Taker  (the ONLY voice to caller)
                                                  │  inline tool: lookup_guidebook ─► Moss  (hot path)
                                                  │  updates ▼
   Command-center dashboard ◄─LiveKit data (or 1 WS)─ in-process IncidentState + asyncio.Queue  (ONE process)
        (live tiles, transcripts,                          ▲ put/await        │ orchestrator reads findings → relays to caller
         findings feed)                                     │
                                       Supervisor (event-driven asyncio loop · LLM planner) — owns ALL dispatch
                                                        ├─► Web Research = asyncio.create_task (no room)
                                                        └─► Agency Liaison ─► LiveKit room N (voice)
                                                                              connect_party(role, mode):
                                                                                sim → dispatch Agency AI
                                                                                sip → create_sip_participant (Twilio)
 LLM for every agent: LiveKit openai.LLM(base_url=TrueFoundry) → MiniMax / Qwen
 Ingest (offline): PDFs ─[Unsiloed]→ chunks ─[index]→ Moss
```

**`connect_party(role, mode)` is the SIP-portability seam:** same room, audio path, transcript capture;
only how the callee joins differs (`sim` dispatches an AI agency agent; `sip` dials a number).

## Build order (vertical slices — always demoable)

**Phase 0 — Scaffold + inbound call (~1h).** Repo skeleton, `.env`, seed JSON loaded, LiveKit inbound
room + the orchestrator/call-taker (STT→LLM→TTS) you can call and talk to. _Milestone: you can call in and talk._

**Phase 1 — Inline guidebook retrieval.** Unsiloed parse ERG → chunks → Moss; build `lookup_guidebook`
as an **inline Moss tool** the orchestrator calls in its own LLM. _Milestone: ask "isolation distance for
UN 1005" → correct ammonia protocol, retrieved from Moss mid-conversation with no perceptible lag._

**Phase 2 — Supervisor + dynamic fan-out.** Orchestrator writes facts to shared `IncidentState`; the
**supervisor** (event-driven asyncio loop, LLM planner) reacts to findings/timers — not just caller turns —
and **spawns workers**: Web Research as an `asyncio.create_task` (no room); voice workers (Phase 3 agency) via
`createDispatch`. Findings go on the queue; the orchestrator relays them to the caller. _(Guidebook stays inline.)_
_Milestone: a worker finding (not a caller turn) triggers the supervisor to spawn a follow-up worker autonomously._

**Phase 3 — Agency liaison + simulated agencies.** `connect_party(sim)` dispatches Agency AIs (Poison
Control, Fire-HazMat) into sub-rooms; liaison relays incident info and returns their response.
_Milestone: full swarm — you see/hear agencies being "called" and responding on the dashboard._

**Phase 4 — Command-center dashboard.** React+Vite + WS feed: incident summary, live tiles per active
agent/call (status + streaming transcript), findings feed, caller transcript. _Milestone: the on-screen demo._

**Phase 5 — De-risk + polish.** Deterministic scenario seed; hardcoded fallback facts if retrieval misses; stream STT/TTS + fast models for latency; distinct voices; **rehearse ≥2×**.
Stretch: flip `connect_party` to **SIP/Twilio** for one real outbound call; AWS deploy; TrueFoundry
observability in the pitch.

## Proposed repo structure (greenfield — new files)

```
mosshack-dispatch-swarm/
  agents/  worker.py (entrypoint, dispatch rules) · call_taker.py (orchestrator: voice only, writes to bus)
           supervisor.py (event-driven loop + LLM planner; owns dispatch)
           liaison.py · agency_sim.py · web_research.py
           llm.py (TrueFoundry client) · voices.py
           connect.py (connect_party: sim|sip) · state.py (in-process IncidentState + asyncio.Queue)
           tools/{guidebook.py (inline Moss), web.py}
  backend/ main.py (FastAPI REST + WS hub)
  seed/    agencies.json · chemicals_fallback.json
  ingest/  parse_erg.py (Unsiloed) · index_moss.py
  frontend/ (Vite+React: IncidentHeader, AgentTile grid, TranscriptPane, FindingsFeed; livekit client)
  .env.example  README.md
```

Note: there is **no `agents/guidebook.py`** — the guidebook is an inline tool (`tools/guidebook.py`), not a dispatched agent.

## Data: what lives where (no database)

- **ERG manual (large, unstructured)** → **Moss** only. Semantic retrieval over the 400-page guidebook is exactly Moss's job.
- **Tiny structured config** → **seed JSON** loaded at startup (a Python dict). _Not_ Moss (fuzzy top-k where you
  need exact, complete config) and _not_ a DB (a handful of records):
  - `agencies.json` — who to call for what + how to reach them (sim now, phone# later); the **supervisor** routes off this.
  - `chemicals_fallback.json` — exact UN# → name/isolation/evac; validates Moss + is the **hardcoded demo fallback**.
- **Live incident state** → in-process `IncidentState` (facts + findings + active workers/calls).
  Want the AWS sponsor checkbox? Use it for hosting/deploy or S3 for the PDF — not a DB you don't need.

## Coordination / latency notes

- **Inline retrieval:** orchestrator calls Moss directly; keep returned snippets tight (top-k small, or a
  short synthesized note) so context stays lean on long calls. Fallback if a path is latency-bound: return
  raw chunks and skip any summarization.
- **Coordination = in-process (no broker):** workers `put` findings on an `asyncio.Queue` + update the shared
  `IncidentState`; the supervisor `await`s the queue, the orchestrator reads state. Dashboard gets updates over
  LiveKit data messages (or one WebSocket). No Redis, no Postgres pub/sub.
- LLM per agent via TrueFoundry: `openai.LLM(base_url=<tf_gateway>, api_key=<tf_key>, model="<minimax|qwen>")`.
  Stream STT partials + TTS chunks; keep Moss calls cheap so
  the fan-out feels instant — _that speed is the demo's whole point._

## Verify first (on-site)

1. **Moss** real ingest + query API/keys + latency (the pitch's `pipecat-moss` is irrelevant — we use LiveKit).
2. **TrueFoundry** fronts MiniMax + Qwen via an **OpenAI-compatible** endpoint; get gateway URL + key.
3. **Unsiloed** parse API/key; grab the public ERG 2024 PDF.
4. **LiveKit** Cloud project + (for the SIP stretch) an outbound trunk via Twilio.
5. Pick a **web search** API (Tavily/Brave/Serp) and a **TTS** provider for distinct voices (Qwen Voice Gen / MiniMax TTS / Cartesia / ElevenLabs).

## Verification (end-to-end)

1. Load seed JSON (agencies, chemical fallback). `curl` TrueFoundry → completions from MiniMax & Qwen.
2. `uv run ingest/parse_erg.py && uv run ingest/index_moss.py` → Moss query "1005" returns ammonia chunk.
3. **Officer quick-query:** call in, ask "isolation distance for UN 1005" → orchestrator answers **inline via
   Moss** with no perceptible lag, no agents spun up (proves retrieval in the hot path + adaptive scaling).
4. **Full incident:** call in → report green-gas tanker on I-95, placard UN 1005, people coughing.
   - Dashboard: orchestrator captures facts and answers protocol inline (Moss → ammonia / 300 ft / 1-mi evac);
     supervisor spawns **Web** (task) + **Liaison** (voice) tiles in parallel.
   - Web → current wind; Liaison → Poison Control & Fire AI respond on their sub-calls.
   - Orchestrator relays consolidated guidance back to you on the line.
5. Latency check: fan-out feels parallel, no long dead air.
6. (Stretch) flip `connect_party` to `sip` → one real outbound call to your phone. Rehearse ≥2×.

## Risks / cut-lines

- **Stage reliability > features.** Deterministic seed + hardcoded fallback facts; cache retrievals.
- Inline retrieval bloating context on a long call is the one downside of inlining — keep snippets tight; summarize or cap top-k if it bites in testing.
- Moss wiring slow → start with a local vector store, swap Moss in once confirmed.
- TrueFoundry/MiniMax/Qwen wiring stalls → point LiveKit at any OpenAI-compatible model, swap gateway in later.
- Keep agency calls **sim** for the live run; SIP only as a rehearsed stretch (telephony fails in noisy venues).
- Cap the swarm size for the demo (2–3 action agents) so the dashboard stays legible and latency stays tight.
- In-process state = one call at a time, lost on crash — fine for a demo; don't add a broker for it.

# To (consider) add:

- Add telephony
- Truefoundry API
- Generalization other than this incident (remove columns in index and agent data passing)
- Reduce latency in LLMs
- More documents, other specialized agents? e.g. medical incident, fire, etc. Will the orchestrator spin up these specialized for just one agent (calltaker) searching through all?
- Take-over call feature
