"""LiveKit agent process — Phase 0+3.

Single entry point for all agent types (orchestrator, liaison, agency-sim). The
AgentServer supports only one rtc_session handler, so role-based routing happens
inside session_handler via the `role` key in job metadata.

Run (from repo root):
    uv run python agents/worker.py download-files   # one-time: VAD + turn-detector
    uv run python agents/worker.py console           # talk in your terminal
    uv run python agents/worker.py dev               # answer an inbound LiveKit room
    uv run python agents/worker.py start             # production
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Match the starter's convention (.env.local) but also accept a plain .env.
load_dotenv(".env.local")
load_dotenv(".env")


def _setup_dev_logging() -> None:
    """Log to stdout and a timestamped file under logs/ — one file per run."""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"dispatch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    fmt = logging.Formatter("%(asctime)s [%(name)-14s] %(levelname)s  %(message)s")
    handlers = [
        logging.StreamHandler(),
        logging.FileHandler(log_path),
    ]
    for h in handlers:
        h.setFormatter(fmt)
    for name in ("supervisor", "web_research", "guidebook", "liaison", "agency_sim", "connect"):
        log = logging.getLogger(name)
        log.setLevel(logging.DEBUG)
        for h in handlers:
            log.addHandler(h)
        log.propagate = False


_setup_dev_logging()

from livekit import rtc  # noqa: E402
from livekit.agents import (  # noqa: E402
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    cli,
    inference,
    room_io,
)
from livekit.plugins import ai_coustics, silero  # noqa: E402
from livekit.plugins.turn_detector.multilingual import MultilingualModel  # noqa: E402

import state as state_mod  # noqa: E402
from agency_sim import run_agency_sim  # noqa: E402
from call_taker import CallTaker  # noqa: E402
from liaison import run_liaison  # noqa: E402
from state import IncidentState  # noqa: E402
from supervisor import Supervisor  # noqa: E402

ORCHESTRATOR_NAME = "dispatch-orchestrator"

server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    # Load Silero VAD once per process (expensive) and reuse across all agent sessions.
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name=ORCHESTRATOR_NAME)
async def session_handler(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    meta = json.loads(ctx.job.metadata or "{}")
    role = meta.get("role", "orchestrator")

    if role == "liaison":
        await run_liaison(ctx, meta)
    elif role == "agency_sim":
        await run_agency_sim(ctx, meta)
    else:
        await _run_orchestrator(ctx)


async def _run_orchestrator(ctx: JobContext) -> None:
    incident = IncidentState()
    state_mod.register_incident(incident)

    session = AgentSession(
        stt=inference.STT(model="deepgram/nova-3", language="multi"),
        tts=inference.TTS(
            model="cartesia/sonic-3", voice="9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"
        ),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
    )

    await session.start(
        agent=CallTaker(incident, room=ctx.room),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=ai_coustics.audio_enhancement(
                    model=ai_coustics.EnhancerModel.QUAIL_VF_S
                ),
            ),
        ),
    )

    asyncio.create_task(Supervisor().run(incident, session, ctx))

    await ctx.connect()

    # Log the caller's phone number when the call comes in over SIP.
    for p in ctx.room.remote_participants.values():
        if p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            phone = p.attributes.get("sip.phoneNumber", "unknown")
            logging.getLogger("orchestrator").info("SIP caller: %s", phone)
            break

    # Dispatcher answers first so the caller hears a live line.
    await session.generate_reply(
        instructions=(
            "Greet the caller as an emergency dispatcher in one short sentence: "
            "'Nine one one, what's your emergency?' Then listen."
        )
    )


if __name__ == "__main__":
    cli.run_app(server)
