"""Per-role Cartesia voice registry for distinct agent voices on dashboard sub-calls.

Orchestrator and liaison share the "dispatcher" voice. Each agency sim gets its own.
All IDs must be valid Cartesia voices available via LiveKit Inference (cartesia/sonic-3).
Swap the UUIDs as needed — the rest of the code only calls get_voice(role).
"""

from __future__ import annotations

VOICES: dict[str, str] = {
    "orchestrator":   "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",  # existing dispatcher voice
    "liaison":        "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",  # same dispatcher voice
    "poison_control": "a0e99841-438c-4a64-b679-ae501e7d6091",  # male medical
    "fire_hazmat":    "c99d36f3-5fce-4b52-b6a6-cd1b5e9e8a11",  # authoritative
    "public_works":   "248be419-c632-4f23-adf1-5324ed7dbf1d",  # professional
}

_DEFAULT = VOICES["orchestrator"]


def get_voice(role: str) -> str:
    return VOICES.get(role, _DEFAULT)
