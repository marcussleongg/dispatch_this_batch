"""Phase 4: Dashboard token server.

Sole responsibility: mint LiveKit access tokens so the browser can join rooms
as a passive viewer (can_publish=False). Also exposes /rooms for auto-discovery.

Run:
    uv run --extra backend uvicorn backend.main:app --reload --port 7880
"""

from __future__ import annotations

import os
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from livekit.api import AccessToken, LiveKitAPI, VideoGrants
from livekit.api import ListRoomsRequest

load_dotenv(".env.local")
load_dotenv(".env")

app = FastAPI(title="Dispatch Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "")
API_KEY = os.getenv("LIVEKIT_API_KEY", "")
API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/token")
def get_token(
    room: str = Query(..., description="LiveKit room name to join"),
    identity: str = Query(default=None),
):
    if not API_KEY or not API_SECRET:
        raise HTTPException(status_code=500, detail="LiveKit credentials not configured")
    if not identity:
        identity = f"dashboard-{uuid.uuid4().hex[:8]}"

    token = (
        AccessToken(api_key=API_KEY, api_secret=API_SECRET)
        .with_identity(identity)
        .with_name("Dashboard")
        .with_grants(
            VideoGrants(
                room_join=True,
                room=room,
                can_publish=False,
                can_subscribe=True,
            )
        )
        .to_jwt()
    )
    return {"token": token, "url": LIVEKIT_URL, "identity": identity}


_SUB_ROOM_SUFFIXES = {"-fire_hazmat", "-poison_control", "-public_works"}


def _is_sub_room(name: str) -> bool:
    return any(name.endswith(s) for s in _SUB_ROOM_SUFFIXES)


@app.get("/rooms")
async def list_rooms():
    """Return active LiveKit room names for dashboard auto-discovery (main rooms only)."""
    async with LiveKitAPI() as client:
        resp = await client.room.list_rooms(ListRoomsRequest())
    rooms = [r.name for r in resp.rooms if not _is_sub_room(r.name)]
    return {"rooms": rooms}
