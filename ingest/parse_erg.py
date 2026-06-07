"""Phase 1 (offline CLI): parse the ERG 2024 PDF with Unsiloed into structured,
LLM-ready chunks for Moss indexing.

Unsiloed REST flow (docs.unsiloed.ai): POST /parse with the file -> job_id, then poll
GET /parse/{job_id} until status == "Succeeded". Each chunk's `embed` field is
RAG-ready markdown, which we use as the document text. Runs on Unsiloed's hosted
cloud — this is an OFFLINE build step, not part of the live call.

Run:
    uv run --extra ingest python ingest/parse_erg.py data/erg_2024.pdf
    uv run --extra ingest python ingest/parse_erg.py --clean-only   # post-process existing JSON
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from collections import Counter
from pathlib import Path

import requests
from dotenv import load_dotenv

logger = logging.getLogger("ingest.parse")

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env.local")
load_dotenv(ROOT / ".env")

BASE_URL = "https://prod.visionapi.unsiloed.ai"
POLL_INTERVAL_S = 15
MAX_POLLS = 250


def parse_pdf(pdf_path: Path, api_key: str) -> list[dict]:
    """Submit the PDF to Unsiloed and poll until the parse job succeeds."""
    with open(pdf_path, "rb") as f:
        resp = requests.post(
            f"{BASE_URL}/parse",
            headers={"api-key": api_key},
            files={"file": (pdf_path.name, f, "application/pdf")},
        )
    resp.raise_for_status()
    job_id = resp.json()["job_id"]
    logger.info("Unsiloed job submitted: %s (%s)", job_id, pdf_path.name)

    poll_start = time.time()
    for attempt in range(1, MAX_POLLS + 1):
        result = requests.get(
            f"{BASE_URL}/parse/{job_id}", headers={"api-key": api_key}
        ).json()
        status = result.get("status")
        elapsed = time.time() - poll_start
        logger.info("poll %d: status=%s elapsed=%.0fs", attempt, status, elapsed)
        if status == "Succeeded":
            return result.get("chunks", [])
        if status == "Failed":
            raise RuntimeError(result.get("message", "Unsiloed parse failed"))
        time.sleep(POLL_INTERVAL_S)
    raise TimeoutError(
        f"Unsiloed parse did not finish within {MAX_POLLS * POLL_INTERVAL_S}s"
    )


_MIN_LEN = 50
_MAX_LEN = 1500
_WINDOW = 1200
_OVERLAP = 200


def _is_noise(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < _MIN_LEN:
        return True
    # OCR corruption: a single word repeating > 15% of all words
    words = stripped.split()
    if len(words) > 20:
        top_count = Counter(words).most_common(1)[0][1]
        if top_count / len(words) > 0.15:
            return True
    return False


def _split_on_headers(doc: dict) -> list[dict]:
    """Split a chunk that contains multiple ## sections into one chunk per section."""
    text = doc["text"]
    if "\n## " not in text:
        return [doc]
    parts = re.split(r"\n(?=## )", text)
    result = []
    for j, part in enumerate(parts):
        part = part.strip()
        if part:
            result.append({"id": f"{doc['id']}-{j}", "text": part, "metadata": doc["metadata"]})
    return result


def _sliding_window(doc: dict) -> list[dict]:
    """Break a chunk that's still too long into overlapping windows."""
    text = doc["text"]
    if len(text) <= _MAX_LEN:
        return [doc]
    chunks, start, j = [], 0, 0
    while start < len(text):
        piece = text[start : start + _WINDOW].strip()
        if piece:
            chunks.append({"id": f"{doc['id']}-w{j}", "text": piece, "metadata": doc["metadata"]})
        j += 1
        start += _WINDOW - _OVERLAP
    return chunks


def _normalize_whitespace(text: str) -> str:
    lines = (line.rstrip() for line in text.splitlines())
    normalized = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", normalized).strip()


def clean(docs: list[dict]) -> list[dict]:
    """Filter noise, normalize whitespace, and split overlong chunks."""
    out: list[dict] = []
    for doc in docs:
        doc = {**doc, "text": _normalize_whitespace(doc["text"])}
        if _is_noise(doc["text"]):
            continue
        for piece in _split_on_headers(doc):
            if _is_noise(piece["text"]):
                continue
            out.extend(_sliding_window(piece))
    return out


_MERGE_TARGET = 1200
_MERGE_OVERLAP = 150


def merge(docs: list[dict]) -> list[dict]:
    """Greedily merge adjacent short chunks up to _MERGE_TARGET chars.

    Adds overlap at merge boundaries only when the incoming chunk has no section
    header — if it starts with '#' it's a distinct section and needs no context
    from the previous chunk.
    """
    out: list[dict] = []
    buf_text = ""
    buf_id = ""
    buf_meta: dict = {}

    for doc in docs:
        if not buf_text:
            buf_text, buf_id, buf_meta = doc["text"], doc["id"], doc["metadata"]
        elif len(buf_text) + 2 + len(doc["text"]) <= _MERGE_TARGET:
            buf_text += "\n\n" + doc["text"]
        else:
            out.append({"id": buf_id, "text": buf_text, "metadata": buf_meta})
            new_starts_section = doc["text"].lstrip().startswith("#")
            prefix = "" if new_starts_section else buf_text[-_MERGE_OVERLAP:] + "\n\n"
            buf_text = prefix + doc["text"]
            buf_id, buf_meta = doc["id"], doc["metadata"]

    if buf_text:
        out.append({"id": buf_id, "text": buf_text, "metadata": buf_meta})

    return out


def to_documents(chunks: list[dict]) -> list[dict]:
    """Map Unsiloed chunks -> {id, text, metadata} (Moss metadata must be strings)."""
    docs: list[dict] = []
    for i, chunk in enumerate(chunks):
        text = (chunk.get("embed") or "").strip()
        if not text:
            continue
        docs.append(
            {
                "id": f"erg-{i}",
                "text": text,
                "metadata": {
                    "source": "erg2024",
                },
            }
        )
    return docs


def main() -> None:
    ap = argparse.ArgumentParser(description="Parse a PDF with Unsiloed -> chunks JSON")
    ap.add_argument("pdf", nargs="?", default=str(ROOT / "data" / "ERG2024.pdf"))
    ap.add_argument("--out", default=str(ROOT / "data" / "erg_chunks.json"))
    ap.add_argument(
        "--clean-only",
        action="store_true",
        help="skip Unsiloed; read existing --out JSON, clean it, write erg_chunks_clean.json",
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    raw_path = Path(args.out)

    if args.clean_only:
        if not raw_path.exists():
            raise SystemExit(f"Raw chunks not found: {raw_path}  (run without --clean-only first)")
        with open(raw_path, encoding="utf-8") as f:
            docs = json.load(f)
        for doc in docs:
            doc["metadata"].pop("segment_type", None)
        before = len(docs)
        docs = clean(docs)
        after_clean = len(docs)
        docs = merge(docs)
        clean_path = raw_path.with_name("erg_chunks_clean.json")
        with open(clean_path, "w", encoding="utf-8") as f:
            json.dump(docs, f, indent=2)
        logger.info(
            "cleaned %d -> %d -> merged %d docs, written to %s",
            before, after_clean, len(docs), clean_path,
        )
        return

    api_key = os.getenv("UNSILOED_API_KEY")
    if not api_key:
        raise SystemExit("UNSILOED_API_KEY not set (add it to .env.local).")

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    started = time.time()
    chunks = parse_pdf(pdf_path, api_key)
    docs = to_documents(chunks)

    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(docs, f, indent=2)

    logger.info(
        "Parsed %d chunks -> %d docs written to %s in %.1fs",
        len(chunks),
        len(docs),
        raw_path,
        time.time() - started,
    )


if __name__ == "__main__":
    main()
