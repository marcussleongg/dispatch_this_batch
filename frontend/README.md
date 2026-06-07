# Command-center dashboard (Phase 4)

Two options:

1. **Reuse the moss-hacker-starter frontend** (recommended). Its Next.js app already
   ships a live "Knowledge Matches" panel driven by `moss_context` data packets
   (`hooks/useMossContextEvents.ts` + `components/app/moss-results-panel.tsx`).
   Repurpose that data-packet channel into the agent-tile grid / findings feed —
   the orchestrator and workers publish LiveKit data messages, the panel renders them.
2. Or scaffold fresh (Vite + React, per plan): `IncidentHeader`, `AgentTile` grid,
   `TranscriptPane`, `FindingsFeed`.

Empty on purpose until Phase 4.
