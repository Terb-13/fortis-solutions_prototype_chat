"""Supabase persistence for Quick Ship estimate wizard sessions (per conversation)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Literal

from fortis_cs_agent import store as _store

logger = logging.getLogger(__name__)

SESSIONS_TABLE = os.getenv("FORTIS_ESTIMATE_SESSIONS_TABLE", "fortis_estimate_sessions")

EstimateSessionStatus = Literal["in_progress", "paused", "completed", "abandoned"]


def _client() -> Any | None:
    return _store.supabase


def fetch_estimate_session(conversation_id: str) -> dict[str, Any] | None:
    """Return the latest session row for this conversation, or None."""
    client = _client()
    if client is None:
        return None
    cid = (conversation_id or "").strip()
    if not cid:
        return None
    try:
        res = (
            client.table(SESSIONS_TABLE)
            .select("*")
            .eq("conversation_id", cid)
            .limit(1)
            .execute()
        )
        if not res.data:
            return None
        row = dict(res.data[0])
        cd = row.get("collected_data")
        if isinstance(cd, str) and cd.strip():
            try:
                row["collected_data"] = json.loads(cd)
            except json.JSONDecodeError:
                row["collected_data"] = {}
        elif cd is None:
            row["collected_data"] = {}
        elif not isinstance(cd, dict):
            row["collected_data"] = {}
        return row
    except Exception:
        logger.exception("fetch_estimate_session failed conversation_id=%s", cid[:16])
        return None


def upsert_estimate_session(
    conversation_id: str,
    *,
    current_step: int,
    collected_data: dict[str, Any],
    status: EstimateSessionStatus,
) -> None:
    """Insert or replace wizard state for a conversation."""
    client = _client()
    if client is None:
        return
    cid = (conversation_id or "").strip()
    if not cid:
        return
    now = datetime.now(timezone.utc).isoformat()
    row: dict[str, Any] = {
        "conversation_id": cid,
        "current_step": max(1, min(5, int(current_step))),
        "collected_data": collected_data,
        "status": status,
        "updated_at": now,
    }
    try:
        client.table(SESSIONS_TABLE).upsert([row], on_conflict="conversation_id").execute()
    except Exception:
        logger.exception("upsert_estimate_session failed conversation_id=%s", cid[:16])


def update_estimate_session_status(conversation_id: str, status: EstimateSessionStatus) -> None:
    client = _client()
    if client is None:
        return
    cid = (conversation_id or "").strip()
    if not cid:
        return
    now = datetime.now(timezone.utc).isoformat()
    try:
        client.table(SESSIONS_TABLE).update({"status": status, "updated_at": now}).eq(
            "conversation_id", cid
        ).execute()
    except Exception:
        logger.exception("update_estimate_session_status failed conversation_id=%s", cid[:16])
