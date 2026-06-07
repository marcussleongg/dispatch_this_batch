"""Per-call live Moss index for queryable findings and supervisor annotations.

Two-track write strategy:
  Local  — SessionIndex (in-memory) for call-taker queries (~1–10 ms, same process).
  Cloud  — MossClient.create_index() on first write, .add_docs() on subsequent writes,
           followed by .load_index() so the cloud index becomes queryable. Liaison agents
           in separate OS processes can then call client.query("live-{incident_id}", ...)
           using the incident_id from their dispatch metadata.

push_index() was removed — it returns HTTP 400 (SDK/server version mismatch). The
direct create_index / add_docs path works correctly.

Three sources write to it during a call:
  web_research  — Tavily wind/weather/conditions findings
  agency_{id}   — guidance relayed back from liaison sub-calls
  supervisor    — dispatch annotations (which agencies were contacted, etc.)

destroy() deletes the cloud index at call end.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid

logger = logging.getLogger("live_conditions")


class LiveConditions:
    def __init__(self, incident_id: str) -> None:
        self._index_name = f"live-{incident_id}"
        self._session = None
        self._client = None
        self._init_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()   # serialize Rust add_docs (not concurrency-safe)
        self._cloud_created = False         # True after first cloud create_index

        pid, key = os.getenv("MOSS_PROJECT_ID"), os.getenv("MOSS_PROJECT_KEY")
        if pid and key:
            from moss import MossClient
            self._client = MossClient(pid, key)
            logger.info("live: MossClient ready for index=%s", self._index_name)
        else:
            logger.warning("live: Moss creds not set — live conditions disabled")

    # ── Local session (call-taker fast path) ─────────────────────────────────

    async def _ensure_session(self) -> bool:
        if self._session is not None:
            return True
        if self._client is None:
            return False
        async with self._init_lock:
            if self._session is not None:
                return True
            try:
                logger.info("live: creating local session index=%s", self._index_name)
                self._session = await self._client.session(self._index_name)
                logger.info(
                    "live: local session ready index=%s doc_count=%d",
                    self._index_name, self._session.doc_count,
                )
                return True
            except Exception:
                logger.exception("live: failed to create local session index=%s", self._index_name)
                return False

    # ── Write ─────────────────────────────────────────────────────────────────

    async def add(self, text: str, source: str) -> None:
        """Write a document to both the local session and the cloud index."""
        if not await self._ensure_session():
            return

        from moss import DocumentInfo
        doc = DocumentInfo(
            id=f"{source}-{uuid.uuid4().hex[:8]}",
            text=text,
            metadata={"source": source, "ts": time.time()},
        )

        # Local write — serialized (Rust inner object is not concurrency-safe).
        async with self._write_lock:
            try:
                added, updated = await self._session.add_docs([doc])
                logger.info(
                    "live: add [local] source=%s added=%d updated=%d doc_count=%d index=%s",
                    source, added, updated, self._session.doc_count, self._index_name,
                )
            except Exception:
                logger.exception("live: add_docs [local] FAILED source=%s index=%s", source, self._index_name)
                return

        # Cloud write — create on first doc, append on subsequent docs, then load.
        await self._cloud_write(doc)

    async def _cloud_write(self, doc) -> None:
        if self._client is None:
            return
        try:
            if not self._cloud_created:
                result = await self._client.create_index(self._index_name, [doc])
                self._cloud_created = True
                logger.info(
                    "live: create [cloud] index=%s job_id=%s",
                    self._index_name, getattr(result, "job_id", "n/a"),
                )
            else:
                result = await self._client.add_docs(self._index_name, [doc])
                logger.info(
                    "live: add [cloud] index=%s job_id=%s",
                    self._index_name, getattr(result, "job_id", "n/a"),
                )
            # Load so the index is immediately queryable by liaison agents.
            await self._client.load_index(self._index_name)
            logger.info("live: load [cloud] index=%s — queryable by liaison agents", self._index_name)
        except Exception as exc:
            logger.warning(
                "live: cloud write failed (local queries still work) index=%s: %s",
                self._index_name, exc,
            )

    # ── Local query (call-taker, same process) ────────────────────────────────

    async def lookup(self, query: str) -> str:
        """Query the local in-memory session (~1–10 ms, no cloud round trip)."""
        if not await self._ensure_session():
            return ""
        try:
            from moss import QueryOptions
            doc_count = self._session.doc_count
            logger.info(
                "live: query [local] %r doc_count=%d index=%s",
                query, doc_count, self._index_name,
            )
            if doc_count == 0:
                logger.info("live: query skipped — no documents indexed yet")
                return ""
            result = await self._session.query(query, QueryOptions(top_k=2))
            docs = getattr(result, "docs", None) or []
            snippets = [(d.text or "").strip() for d in docs if (d.text or "").strip()]
            scores = ", ".join(f"{getattr(d, 'score', 0.0):.3f}" for d in docs[:2])
            logger.info("live: query result %d hits scores=[%s] for %r", len(docs), scores, query)
            return "\n\n".join(snippets) if snippets else ""
        except Exception:
            logger.exception("live: query FAILED for %r index=%s", query, self._index_name)
            return ""

    # ── Cleanup ───────────────────────────────────────────────────────────────

    async def destroy(self) -> None:
        """Delete the cloud index at call end."""
        if self._client is None:
            return
        try:
            await self._client.delete_index(self._index_name)
            logger.info("live: deleted cloud index=%s", self._index_name)
        except Exception:
            logger.exception("live: failed to delete cloud index=%s", self._index_name)
