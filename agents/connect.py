"""Phase 3: connect_party() — the SIP-portability seam.

sim mode  → dispatch a Liaison voice agent into a new LiveKit sub-room via
            AgentDispatchService. The sub-room name is {incident_id}-{agency_id}.
sip mode  → stub (Twilio stretch goal — flip mode + add trunk creds, no logic change).
"""

from __future__ import annotations

import json
import logging

from livekit.protocol.agent_dispatch import CreateAgentDispatchRequest

from state import IncidentState

logger = logging.getLogger("connect")

ORCHESTRATOR_NAME = "dispatch-orchestrator"


async def connect_party(
    ctx,
    incident: IncidentState,
    agency: dict,
    mode: str = "sim",
) -> None:
    """Dispatch a liaison agent for the given agency into a dedicated sub-room.

    Args:
        ctx: JobContext from the orchestrator (provides ctx.api.agent_dispatch).
        incident: the live IncidentState for this call.
        agency: agency dict from seed/agencies.json.
        mode: "sim" (default) dispatches an AI agency; "sip" is a future stretch goal.
    """
    if mode != "sim":
        raise NotImplementedError("SIP mode is a stretch goal — keep mode='sim' for the demo")

    room_name = f"{incident.incident_id}-{agency['id']}"
    dispatch = await ctx.api.agent_dispatch.create_dispatch(
        CreateAgentDispatchRequest(
            agent_name=ORCHESTRATOR_NAME,
            room=room_name,
            metadata=json.dumps({
                "role": "liaison",
                "agency_id": agency["id"],
                "incident_id": incident.incident_id,
            }),
        )
    )
    logger.info(
        "dispatched liaison for %s → room=%s dispatch_id=%s",
        agency["id"],
        room_name,
        dispatch.id,
    )
