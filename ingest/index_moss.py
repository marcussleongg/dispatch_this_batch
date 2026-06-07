"""Phase 1 (offline CLI): build the Moss `guidebook` index from the curated seed
corpus plus the Unsiloed-parsed ERG chunks, so the orchestrator can query
"isolation distance for UN 1005" inline mid-call.

Mirrors the moss-hacker-starter's src/create_index.py. OFFLINE build step.

Run:
    uv run python ingest/index_moss.py                       # build
    uv run python ingest/index_moss.py --recreate            # delete + rebuild
    uv run python ingest/index_moss.py --query "UN 1005"     # verify retrieval
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from moss import DocumentInfo, MossClient, QueryOptions

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))  # reuse agents/seed_data.py
load_dotenv(ROOT / ".env.local")
load_dotenv(ROOT / ".env")

import seed_data  # noqa: E402

logger = logging.getLogger("ingest.index")

INDEX_NAME = os.getenv("MOSS_INDEX_NAME", "guidebook")
MODEL_ID = os.getenv("MOSS_MODEL_ID", "moss-minilm")
_ERG_CLEAN = ROOT / "data" / "erg_chunks_clean.json"
_ERG_RAW = ROOT / "data" / "erg_chunks.json"
ERG_CHUNKS = _ERG_CLEAN if _ERG_CLEAN.exists() else _ERG_RAW


def _client() -> MossClient:
    pid, key = os.getenv("MOSS_PROJECT_ID"), os.getenv("MOSS_PROJECT_KEY")
    if not pid or not key:
        raise SystemExit("MOSS_PROJECT_ID / MOSS_PROJECT_KEY not set (add to .env.local).")
    return MossClient(pid, key)


def _to_doc(entry: dict) -> DocumentInfo:
    md = {str(k): str(v) for k, v in entry.get("metadata", {}).items()}
    return DocumentInfo(id=entry["id"], text=entry["text"], metadata=md)


def _docs() -> list[DocumentInfo]:
    docs = [_to_doc(e) for e in seed_data.guidebook()]
    seed_n = len(docs)
    erg_n = 0
    if ERG_CHUNKS.exists():
        with open(ERG_CHUNKS, encoding="utf-8") as f:
            erg = [_to_doc(e) for e in json.load(f)]
        docs += erg
        erg_n = len(erg)
    else:
        logger.info("no %s yet — run parse_erg.py for ERG breadth", ERG_CHUNKS.name)
    logger.info("corpus: %d seed + %d ERG = %d docs", seed_n, erg_n, len(docs))
    return docs


async def build(recreate: bool) -> None:
    client = _client()
    docs = _docs()
    if recreate:
        try:
            await client.delete_index(INDEX_NAME)
            logger.info("deleted existing index '%s'", INDEX_NAME)
        except Exception:
            logger.info("no existing index '%s' to delete", INDEX_NAME)
    started = time.time()
    logger.info("creating Moss index '%s' (model=%s)...", INDEX_NAME, MODEL_ID)
    result = await client.create_index(INDEX_NAME, docs, MODEL_ID)
    logger.info(
        "index '%s' created: job=%s, docs=%s, %.1fs",
        result.index_name,
        result.job_id,
        result.doc_count,
        time.time() - started,
    )


async def run_query(text: str, top_k: int) -> None:
    client = _client()
    await client.load_index(INDEX_NAME)
    result = await client.query(INDEX_NAME, text, QueryOptions(top_k=top_k))
    docs = getattr(result, "docs", None) or []
    logger.info(
        "query %r -> %d docs in %.0fms",
        text,
        len(docs),
        getattr(result, "time_taken_ms", 0) or 0,
    )
    for d in docs:
        score = f"{d.score:.3f}" if d.score is not None else "?"
        print(f"\n[{score}] {d.id} ({d.metadata})\n{(d.text or '')[:300]}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build / query the Moss guidebook index")
    ap.add_argument("--query", help="run one query against the index instead of building")
    ap.add_argument("--recreate", action="store_true", help="delete the index before building")
    ap.add_argument("--top-k", type=int, default=3)
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    if args.query:
        asyncio.run(run_query(args.query, args.top_k))
    else:
        asyncio.run(build(args.recreate))


if __name__ == "__main__":
    main()
