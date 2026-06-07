"""Phase 2: Web Research worker — asyncio.create_task, no LiveKit room.

Pulls live conditions (wind, weather, news) for the incident location and chemical,
then puts a FINDING on the event bus so the supervisor relays it to the caller.
Also indexes the finding into the per-call live Moss index (incident.live) so the
call-taker can recall it via lookup_conditions later in the call.
Degrades gracefully when TAVILY_API_KEY is not set.
"""

from __future__ import annotations

import asyncio
import logging
import os

from state import EventType, IncidentState

logger = logging.getLogger("web_research")

TAVILY_URL = "https://api.tavily.com/search"


def _build_query(location: str | None, chemical: str | None, reason: str) -> str:
    """Build the most useful Tavily query given whatever facts are confirmed."""
    if location and chemical:
        return (
            f"current wind direction speed weather near {location} "
            f"{chemical} hazmat emergency"
        )
    if location:
        return f"current wind direction speed weather conditions near {location}"
    if chemical:
        return (
            f"{chemical} hazardous material emergency spill current news "
            f"evacuation conditions"
        )
    base = reason if reason else "hazmat emergency"
    return f"{base} current weather wind direction emergency conditions"


async def web_research(incident: IncidentState, reason: str = "") -> None:
    facts = incident.facts
    location = facts.get("location")
    chemical = facts.get("chemical") or facts.get("un_number")

    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        context = " ".join(filter(None, [
            f"near {location}" if location else "",
            f"involving {chemical}" if chemical else "",
        ])) or "at the incident site"
        finding = (
            f"Live wind data unavailable. As a precaution, all responders {context} "
            f"should stay upwind and uphill of the release."
        )
        logger.warning("TAVILY_API_KEY not set; emitting synthetic finding")
        _record(incident, finding)
        return

    query = _build_query(location, chemical, reason)
    logger.info("web_research query: %r", query)
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
            loc_str = location or "the incident site"
            finding = f"No current conditions data found near {loc_str}."
        logger.info("web_research finding (%.80s)", finding)

    except Exception:
        logger.exception("web_research HTTP call failed")
        loc_str = location or "the incident site"
        chem_str = chemical or "the substance"
        finding = (
            f"Live conditions data unavailable. Advise responders to assume worst-case "
            f"wind spread for {chem_str} near {loc_str}."
        )

    _record(incident, finding)


_bg_tasks: set[asyncio.Task] = set()


def _keep(t: asyncio.Task) -> None:
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)


def _record(incident: IncidentState, summary: str) -> None:
    incident.add_finding("web_research", summary)
    _keep(asyncio.create_task(
        incident.emit(EventType.FINDING, source="web_research", summary=summary)
    ))
    if incident.live is not None:
        _keep(asyncio.create_task(incident.live.add(summary, source="web_research")))
