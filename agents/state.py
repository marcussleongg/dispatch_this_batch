"""In-process incident state + event bus (the only coordination layer — no broker).

Per the plan: orchestrator + supervisor + workers run in ONE process and coordinate
via a shared ``IncidentState`` and an ``asyncio.Queue``. No Redis, no Postgres.

Phase 0 only constructs/holds this; the supervisor (Phase 2) consumes the queue.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    FACT = "fact"               # orchestrator extracted a new incident fact
    FINDING = "finding"         # a worker (web/agency) returned a result
    WORKER_STATUS = "worker"    # a dispatched worker changed state
    TIMER = "timer"             # a scheduled tick the supervisor reacts to


@dataclass
class Event:
    type: EventType
    payload: dict[str, Any]
    ts: float = field(default_factory=time.time)


@dataclass
class IncidentState:
    """Live state for a single active call. One call at a time (fine for the demo)."""

    incident_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    facts: dict[str, Any] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)
    workers: dict[str, str] = field(default_factory=dict)  # worker_id -> status

    # The event bus the supervisor awaits (Phase 2). Created lazily so it binds
    # to the running loop, not import-time.
    events: asyncio.Queue[Event] = field(default_factory=asyncio.Queue)

    async def emit(self, type: EventType, **payload: Any) -> None:
        await self.events.put(Event(type=type, payload=payload))

    def add_fact(self, key: str, value: Any) -> None:
        self.facts[key] = value

    def add_finding(self, source: str, summary: str, **extra: Any) -> None:
        self.findings.append({"source": source, "summary": summary, **extra})
