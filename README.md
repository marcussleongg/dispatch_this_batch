# AI Emergency Dispatch Swarm (MossHack)

An AI emergency-dispatch call center. One inbound 911-style call is answered by an
AI call-taker (the **single voice** to the caller); a **supervisor** then spins up a
**swarm of parallel action agents** mid-call — live web research + simulated agency
calls — while a **command-center dashboard** streams every agent and transcript.

- **Retrieval is inline** (Moss in the hot path) — the guidebook is a tool
  (`lookup_guidebook`), not a separate agent.
- **Separate agents are reserved for concurrent _actions_** (web research, agency
  calls), dispatched live by the supervisor via LiveKit.
- **One process, no broker** — orchestrator + supervisor + workers coordinate via an
  in-process `IncidentState` + `asyncio.Queue`. No DB; static config is seed JSON.

See [`plan.md`](./plan.md) for the full design and build order.

Built on the conventions of the official
[`moss-hacker-starter`](https://github.com/livekit-examples/moss-hacker-starter):
the `AgentServer` + `@server.rtc_session` pattern and **LiveKit Inference** for
STT/LLM/TTS, so the only credentials you need are **LiveKit + Moss**.

## Status

- **Phase 0 ✅ — Scaffold + inbound call.** Repo skeleton, seed JSON + loader, and a
  working LiveKit voice agent (STT → LLM → TTS) you can call in and talk to.
- Phases 1–5 (inline Moss guidebook, supervisor fan-out, agency sims, dashboard,
  polish) are stubbed — see the `TODO(Phase N)` markers.

## Quickstart (Phase 0)

Prereqs: [`uv`](https://docs.astral.sh/uv/), a LiveKit Cloud project, and (for
Phase 1+) a [Moss](https://portal.usemoss.dev) project. **No** Deepgram / Cartesia /
OpenAI keys — STT, LLM, and TTS all run through LiveKit Inference.

```bash
cp env.example .env.local        # then fill in LIVEKIT_* (lk app env -w -d .env.local)
uv sync                          # install deps into .venv

uv run python agents/worker.py download-files   # one-time: VAD + turn detector
uv run python agents/worker.py console           # talk in your terminal
```

`console` mode is the fastest way to hit the **Phase 0 milestone** — you speak, the
dispatcher answers. For `dev` mode (`uv run python agents/worker.py dev`), point a
frontend / SIP inbound at the same LiveKit project, dispatching the agent name
`dispatch-orchestrator`, so a call lands in a room the agent joins.

### Mapping from the `moss-hacker-starter` README

This repo uses a **custom uv layout**, not the starter's `pnpm` + `agent-py/` +
`frontend/` scaffold. If you were handed the starter's README, here's the 1:1
translation (also wrapped as `make` targets — `make setup`, `make dev`, …):

| Starter README (`pnpm`) | Here (uv) |
| --- | --- |
| `pnpm setup` | `uv sync` &nbsp;(`make setup`) — agent only; no frontend yet |
| keys → `agent-py/.env.local` + `frontend/.env.local` | one root `.env.local` (or `.env`) |
| `pnpm moss:index` | `uv run --extra ingest python ingest/index_moss.py` (`make index`) — **Phase 1, stub** |
| `pnpm dev` | `uv run python agents/worker.py dev` (`make dev`) — agent only |
| `pnpm agent:py:console` | `uv run python agents/worker.py console` (`make console`) |

Note `make index` is a Phase-1 placeholder — the Moss index isn't built yet, so for
the **Phase 0** milestone you only need `setup` → keys → `download` → `console`.

### Config / model routing

Credentials live in `.env.local` (see `env.example`). STT (`deepgram/nova-3`), LLM
(`openai/gpt-5.2-chat-latest`), and TTS (`cartesia/sonic-3`) are LiveKit Inference
model strings set in `agents/worker.py` — swap the names to change models, no keys
required.

**TrueFoundry seam (locked plan decision, off by default):** set
`TRUEFOUNDRY_BASE_URL` + `TRUEFOUNDRY_API_KEY` and run `uv sync --extra truefoundry`
to route the LLM through the OpenAI-compatible gateway (MiniMax / Qwen) instead of
Inference — `agents/llm.py` picks the path; no code change.

## Layout

```
agents/    worker.py (AgentServer + rtc_session) · call_taker.py (orchestrator, voice only)
           llm.py (Inference / TrueFoundry seam) · state.py (IncidentState + queue)
           seed_data.py (loads seed/*.json)
           supervisor.py · liaison.py · agency_sim.py · web_research.py
           connect.py · voices.py · tools/{guidebook.py, web.py}   ← later phases
backend/   main.py (FastAPI REST + WS hub)                          ← Phase 4
seed/      agencies.json · chemicals_fallback.json
ingest/    parse_erg.py (Unsiloed) · index_moss.py                  ← Phase 1
frontend/  dashboard (reuse the starter's Next.js app)              ← Phase 4
```
