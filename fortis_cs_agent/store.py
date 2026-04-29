"""Persist estimate payloads so GET /estimate-pdf/{id} can regenerate the PDF deterministically.

Supabase DDL::

    create table if not exists fortis_estimates (
      id uuid primary key,
      payload jsonb not null,
      created_at timestamptz default now()
    );
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fortis_cs_agent.estimate_models import EstimateRequest

logger = logging.getLogger(__name__)

EST_TABLE = os.getenv("FORTIS_ESTIMATES_TABLE", "fortis_estimates")

_LOCAL: dict[str, dict[str, Any]] = {}


def _sb() -> Any | None:
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        return None
    url = os.getenv("SUPABASE_URL", "https://vapnbelrpaxeafospalc.supabase.co")
    from supabase import create_client

    return create_client(url, key)


def save_estimate_snapshot(est: EstimateRequest) -> None:
    """Store enough data to rebuild the estimate + PDF."""
    pid = str(est.estimate_id)
    payload = est.model_dump(mode="json")
    client = _sb()
    if client is not None:
        try:
            client.table(EST_TABLE).upsert({"id": pid, "payload": payload}).execute()
            logger.info("stored estimate snapshot in Supabase: %s", pid)
            return
        except Exception:
            logger.exception("Supabase estimate upsert failed; using in-memory fallback.")
    _LOCAL[pid] = payload


def load_estimate_snapshot(estimate_id: str) -> EstimateRequest | None:
    payload: dict[str, Any] | None = None
    client = _sb()
    if client is not None:
        try:
            res = (
                client.table(EST_TABLE)
                .select("payload")
                .eq("id", estimate_id)
                .limit(1)
                .execute()
            )
            if res.data:
                payload = res.data[0].get("payload")
        except Exception:
            logger.exception("Supabase estimate load failed.")
    if payload is None:
        payload = _LOCAL.get(estimate_id)

    if not payload:
        return None
    try:
        return EstimateRequest.model_validate(payload)
    except Exception:
        logger.exception("Invalid estimate snapshot for %s", estimate_id)
        return None


def clear_memory_store() -> None:
    """Test hook only."""
    _LOCAL.clear()
