"""Phase 2: Web Research worker — asyncio.create_task, no LiveKit room.

Pulls live conditions (wind, weather, news) for the incident location and chemical,
then puts a FINDING on the event bus so the supervisor relays it to the caller.
Degrades gracefully when TAVILY_API_KEY is not set.
"""

from __future__ import annotations

import logging
import os

from state import EventType, IncidentState

logger = logging.getLogger("web_research")

TAVILY_URL = "https://api.tavily.com/search"


async def web_research(incident: IncidentState) -> None:
    facts = incident.facts
    location = facts.get("location", "unknown location")
    chemical = facts.get("chemical") or facts.get("un_number") or "unknown chemical"

    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        finding = (
            f"Live wind data unavailable. As a precaution, all responders near "
            f"{location} should stay upwind and uphill of the {chemical} release."
        )
        logger.warning("TAVILY_API_KEY not set; emitting synthetic finding")
        _record(incident, finding)
        return

    query = f"current wind direction and speed weather conditions near {location}"
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                TAVILY_URL,
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": 3,
                    "include_answer": True,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        finding = data.get("answer", "").strip()
        if not finding:
            results = data.get("results", [])
            finding = results[0].get("content", "")[:300].strip() if results else ""
        if not finding:
            finding = f"No current conditions data found near {location}."
        logger.info("web_research finding (%.80s)", finding)

    except Exception:
        logger.exception("web_research HTTP call failed")
        finding = (
            f"Live conditions data unavailable. Advise responders to assume worst-case "
            f"wind spread for {chemical} near {location}."
        )

    _record(incident, finding)


def _record(incident: IncidentState, summary: str) -> None:
    incident.add_finding("web_research", summary)
    # emit is async; schedule it as a task so this sync helper stays simple.
    import asyncio
    asyncio.create_task(
        incident.emit(EventType.FINDING, source="web_research", summary=summary)
    )
