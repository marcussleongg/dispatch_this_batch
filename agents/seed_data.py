"""Load the tiny structured config (agency directory, chemical fallback) from
``seed/*.json`` at startup — NOT Moss (we need exact, complete config) and NOT a
DB (a handful of records). See plan: "Data: what lives where".
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

SEED_DIR = Path(__file__).resolve().parent.parent / "seed"


def _load(filename: str) -> dict[str, Any]:
    with open(SEED_DIR / filename, encoding="utf-8") as f:
        return json.load(f)


@functools.lru_cache(maxsize=1)
def agencies() -> list[dict[str, Any]]:
    """Agency directory the supervisor routes off of (Phase 2/3)."""
    return _load("agencies.json")["agencies"]


@functools.lru_cache(maxsize=1)
def chemicals() -> dict[str, dict[str, Any]]:
    """UN# -> chemical record. Validates Moss + hardcoded demo fallback (Phase 1)."""
    return _load("chemicals_fallback.json")["chemicals"]


@functools.lru_cache(maxsize=1)
def guidebook() -> list[dict[str, Any]]:
    """Curated ERG corpus ({id, text, metadata}) indexed into Moss alongside the
    Unsiloed-parsed ERG. Deterministic coverage of the demo chemicals (Phase 1)."""
    with open(SEED_DIR / "guidebook.json", encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    # Quick smoke check: `python agents/seed_data.py`
    print(f"agencies: {[a['id'] for a in agencies()]}")
    print(f"chemicals: {list(chemicals())}")
    print(f"guidebook: {[d['id'] for d in guidebook()]}")
