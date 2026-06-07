"""Cloud-side query helper for liaison/agency-sim agents (separate OS processes).

The orchestrator process writes to live-{incident_id} via LiveConditions.
Liaison and agency-sim processes query it here using the incident_id from
their dispatch metadata.

The index is queryable after LiveConditions._cloud_write() has called
load_index() — typically within a few seconds of the first web research
finding landing.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("live_conditions")


async def query_live_index(incident_id: str, query: str, top_k: int = 2) -> str:
    """Query the live cloud index for this incident. Returns empty string if unavailable."""
    pid, key = os.getenv("MOSS_PROJECT_ID"), os.getenv("MOSS_PROJECT_KEY")
    if not pid or not key:
        return ""
    try:
        from moss import MossClient, QueryOptions
        client = MossClient(pid, key)
        index_name = f"live-{incident_id}"
        result = await client.query(index_name, query, QueryOptions(top_k=top_k))
        docs = getattr(result, "docs", None) or []
        snippets = [(d.text or "").strip() for d in docs if (d.text or "").strip()]
        scores = ", ".join(f"{getattr(d, 'score', 0.0):.3f}" for d in docs[:top_k])
        logger.info(
            "live: cloud query %r → %d hits scores=[%s] index=%s",
            query, len(docs), scores, index_name,
        )
        return "\n\n".join(snippets) if snippets else ""
    except Exception as exc:
        logger.warning("live: cloud query failed for %r index=live-%s: %s", query, incident_id, exc)
        return ""
