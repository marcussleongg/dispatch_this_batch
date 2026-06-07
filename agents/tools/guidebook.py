"""Phase 1: inline `lookup_guidebook` tool logic — ERG protocol lookup over Moss.

A TOOL, not a dispatched agent (retrieval stays in the conversational hot path —
the Moss thesis). The CallTaker exposes ``lookup()`` as a ``@function_tool``; this is
the direct analog of the moss-hacker-starter's ``search_knowledge``.

Degrades gracefully: if Moss creds are missing, or a query misses / errors, it falls
back to ``seed/chemicals_fallback.json`` (exact UN# config = the hardcoded demo
fallback). Every Moss query is logged (query, hits, top score, latency) so you can
watch retrieval work during a live call.
"""

from __future__ import annotations

import logging
import os
import re

import seed_data

logger = logging.getLogger("guidebook")

UN_RE = re.compile(r"\b(\d{4})\b")
TOP_K = 3
# Blend semantic + keyword: UN numbers and ERG guide numbers are exact identifiers,
# so leaning toward keyword (BM25) improves exact-match ranking.
ALPHA = 0.5


class Guidebook:
    def __init__(self) -> None:
        self._index = os.getenv("MOSS_INDEX_NAME", "guidebook")
        self._chemicals = seed_data.chemicals()
        self._client = None
        self._loaded = False

        pid, key = os.getenv("MOSS_PROJECT_ID"), os.getenv("MOSS_PROJECT_KEY")
        if pid and key:
            from moss import MossClient

            self._client = MossClient(pid, key)
        else:
            logger.warning(
                "Moss creds not set; guidebook will use the seed fallback only"
            )

    async def preload(self) -> None:
        """Warm the Moss index so the first query is fast (called from on_enter)."""
        if self._client is None or self._loaded:
            return
        try:
            await self._client.load_index(self._index)
            self._loaded = True
            logger.info("Moss index '%s' loaded", self._index)
        except Exception:
            logger.exception("Failed to preload Moss index '%s'; will retry on use", self._index)

    async def lookup(self, query: str) -> str:
        """Query Moss for ERG guidance; fall back to exact UN# config on a miss."""
        if self._client is not None:
            try:
                from moss import QueryOptions

                result = await self._client.query(
                    self._index, query, QueryOptions(top_k=TOP_K, alpha=ALPHA)
                )
                docs = getattr(result, "docs", None) or []
                top_score = (
                    f"{docs[0].score:.3f}"
                    if docs and docs[0].score is not None
                    else "n/a"
                )
                logger.info(
                    "Moss query %r -> %d hits, top_score=%s, %.0fms",
                    query,
                    len(docs),
                    top_score,
                    getattr(result, "time_taken_ms", 0) or 0,
                )
                for d in docs:
                    logger.debug("  hit %s score=%s: %s", d.id, d.score, (d.text or "")[:120])

                snippets = [(d.text or "").strip() for d in docs if (d.text or "").strip()]
                if snippets:
                    return "\n\n".join(snippets[:TOP_K])
                logger.warning("Moss returned no hits for %r; using seed fallback", query)
            except Exception:
                logger.exception("Moss query failed for %r; using seed fallback", query)

        return self._fallback(query)

    def _fallback(self, query: str) -> str:
        match = UN_RE.search(query)
        if match:
            un = match.group(1)
            rec = self._chemicals.get(un)
            if rec:
                logger.info("fallback: matched UN %s (%s)", rec.get("un"), rec.get("name"))
                return self._format(rec)
            logger.warning("fallback: UN %s not in fallback table", un)
        else:
            logger.warning("fallback: no UN number found in %r", query)
        return (
            "I don't have a specific protocol on hand for that. Give me the UN number "
            "on the placard and I'll pull the exact isolation and evacuation distances."
        )

    @staticmethod
    def _format(rec: dict) -> str:
        return (
            f"UN {rec.get('un')}, {rec.get('name')} (ERG Guide {rec.get('erg_guide')}). "
            f"Initial isolation {rec.get('initial_isolation_ft')} feet in all directions. "
            f"Protective evacuation about {rec.get('protective_evacuation_mi')} mile(s) "
            f"downwind. {rec.get('actions', '')}"
        ).strip()
