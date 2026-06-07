"""Phase 1: inline `lookup_guidebook` tool — ERG protocol lookup over Moss.

A TOOL, not a dispatched agent (retrieval stays in the conversational hot path —
the Moss thesis). Built as a `@function_tool` on the CallTaker, the direct analog
of the starter's `search_knowledge`:

    result = await self._moss.query(MOSS_INDEX_NAME, query, QueryOptions(top_k=3))
    snippets = [d.text for d in result.docs]

Keep top_k small / snippets tight so context stays lean on long calls. Fall back to
seed/chemicals_fallback.json on a miss (exact UN# config + hardcoded demo fallback).
Optionally publish a moss_context data packet for the dashboard (Phase 4).
TODO(Phase 1).
"""
