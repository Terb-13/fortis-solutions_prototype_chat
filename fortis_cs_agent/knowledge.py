"""
Supabase-backed knowledge retrieval for the CS agent.

Expects an optional table `fortis_knowledge_chunks` with at least:
- id (uuid)
- title (text, nullable)
- body (text)
- tags (text[] or jsonb, optional)

Adjust table names via env when extending.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from supabase import Client

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
_SUPABASE_RESOLVED_URL = SUPABASE_URL or "https://vapnbelrpaxeafospalc.supabase.co"

supabase: Client | None = None
if SUPABASE_KEY:
    from supabase import create_client

    supabase = create_client(_SUPABASE_RESOLVED_URL, SUPABASE_KEY)
else:
    logger.warning("SUPABASE_SERVICE_ROLE_KEY missing — knowledge retrieval disabled.")

_KNOWLEDGE_TABLE = os.getenv("FORTIS_KNOWLEDGE_TABLE", "fortis_knowledge_chunks")
_MAX_ROWS = int(os.getenv("FORTIS_KNOWLEDGE_LIMIT", "8"))


def _get_client() -> Client | None:
    return supabase


def retrieve_knowledge_snippets(query: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    """
    Fetch relevant knowledge rows for augmentation into the chat prompt.

    Uses simple ILIKE on body/title when full-text RPC is unavailable.
    """
    client = _get_client()
    if client is None:
        return []

    q = query.strip()
    if len(q) < 2:
        return []

    cap = limit if limit is not None else _MAX_ROWS
    tokens = [t for t in re.split(r"\s+", q) if len(t) > 2][:6]

    try:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for token in tokens:
            pattern = f"%{token}%"
            res = (
                client.table(_KNOWLEDGE_TABLE)
                .select("id, title, body, tags")
                .ilike("body", pattern)
                .limit(cap)
                .execute()
            )
            for row in res.data or []:
                rid = str(row.get("id", id(row)))
                if rid not in seen:
                    seen.add(rid)
                    rows.append(row)
            if len(rows) >= cap:
                break
        return rows[:cap]
    except Exception:
        logger.exception("Knowledge retrieval failed — continuing without snippets.")
        return []


def format_knowledge_context(rows: list[dict[str, Any]]) -> str:
    """Render DB rows into a short block for system or user augmentation."""
    if not rows:
        return ""

    blocks: list[str] = []
    for r in rows:
        title = (r.get("title") or "").strip()
        body = (r.get("body") or "").strip()
        if not body:
            continue
        head = f"**{title}**\n" if title else ""
        blocks.append(head + body)

    if not blocks:
        return ""

    joined = "\n\n---\n\n".join(blocks)
    return "Internal reference (Fortis knowledge base):\n" + joined
