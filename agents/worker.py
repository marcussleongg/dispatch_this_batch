"""LiveKit agent process — Phase 0.

Answers an inbound LiveKit call and runs the orchestrator/call-taker voice loop
(STT -> LLM -> TTS) so you can call in and talk. Uses the `AgentServer` +
`@server.rtc_session` pattern and LiveKit Inference from the moss-hacker-starter,
so the only credentials needed are LIVEKIT_* (plus MOSS_* once Phase 1 lands).

Run (from repo root):
    uv run python agents/worker.py download-files   # one-time: VAD + turn-detector
    uv run python agents/worker.py console           # talk in your terminal
    uv run python agents/worker.py dev               # answer an inbound LiveKit room
    uv run python agents/worker.py start             # production

Later phases register more agent *types* on this server (liaison, agency sims) and
start the supervisor's event-driven loop alongside the session.
"""

from __future__ import annotations

from dotenv import load_dotenv

# Match the starter's convention (.env.local) but also accept a plain .env.
load_dotenv(".env.local")
load_dotenv(".env")

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

from call_taker import CallTaker  # noqa: E402
from state import IncidentState  # noqa: E402

# Dispatch name a frontend / SIP trunk targets to reach the orchestrator.
ORCHESTRATOR_NAME = "dispatch-orchestrator"

server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    # Load Silero VAD once per process (expensive) and reuse across sessions.
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name=ORCHESTRATOR_NAME)
async def orchestrator(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    incident = IncidentState()

    session = AgentSession(
        # STT / TTS via LiveKit Inference — no provider keys. (LLM is on the Agent.)
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

    await ctx.connect()

    # Dispatcher answers first so the caller hears a live line.
    await session.generate_reply(
        instructions=(
            "Greet the caller as an emergency dispatcher in one short sentence: "
            "'Nine one one, what's your emergency?' Then listen."
        )
    )


if __name__ == "__main__":
    cli.run_app(server)
