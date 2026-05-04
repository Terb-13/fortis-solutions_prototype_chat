"""
Deterministic Estimate Mode: capture quote fields sequentially, optionally bypassing Grok.

State persists on the assistant message ``meta["estimate_flow"]``.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from fortis_cs_agent.estimate_detector import is_estimate_request
from fortis_cs_agent.knowledge import retrieve_pricing

logger = logging.getLogger(__name__)

_STEPS = ("product_details", "business_name", "contact_name", "email", "address")
_DEFAULT_NOTES = "This quote does not include shipping or taxes. Prices are valid for 30 days."


def _qty_to_cost_key(qty: int) -> str:
    if qty >= 5000:
        return "cost_5000"
    if qty >= 4000:
        return "cost_4000"
    if qty >= 3000:
        return "cost_3000"
    if qty >= 2500:
        return "cost_2500"
    if qty >= 2000:
        return "cost_2000"
    if qty >= 1500:
        return "cost_1500"
    if qty >= 1000:
        return "cost_1000"
    if qty >= 500:
        return "cost_500"
    if qty >= 250:
        return "cost_250"
    return "cost_100"


def _extract_quantity(text_lower: str) -> int | None:
    for pat in (
        r"\bquantity\s*(?:is|are|=|:)?\s*(\d{1,6})\b",
        r"\bqty\s*(?:is|are|=|:)?\s*(\d{1,6})\b",
        r"\b(\d{1,6})\s*(?:labels|units)\b",
    ):
        m = re.search(pat, text_lower)
        if m:
            q = int(m.group(1))
            if 1 <= q <= 999_999:
                return q
    m = re.search(r"\b(\d{2,6})\b", text_lower)
    if m:
        q = int(m.group(1))
        if 10 <= q <= 999_999:
            return q
    return None


def _extract_dimensions(text_lower: str) -> bool:
    return re.search(r"(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)", text_lower) is not None


def _looks_like_email(s: str) -> bool:
    s = (s or "").strip()
    return "@" in s and "." in s.split("@", 1)[-1]


def _quote_absolute_url(estimate_id: str) -> str:
    pub = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    tail = f"/quote/{estimate_id}"
    return f"{pub}{tail}" if pub else tail


def latest_estimate_flow_snapshot(history_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], int] | None:
    """Return draft + pending step index (which field THIS user reply should fill)."""
    for row in reversed(history_rows):
        if row.get("role") != "assistant":
            continue
        meta_outer = row.get("meta") or {}
        if not isinstance(meta_outer, dict):
            continue
        block = meta_outer.get("estimate_flow")
        if not isinstance(block, dict):
            continue
        if block.get("active") is not True:
            continue
        draft = block.get("draft") or {}
        if not isinstance(draft, dict):
            draft = {}
        idx = int(block.get("pending_step_index", 0))
        idx = max(0, min(idx, len(_STEPS) - 1))
        return draft, idx
    return None


def _meta_payload(draft: dict[str, Any], pending_step_index: int, *, active: bool = True) -> dict[str, Any]:
    return {
        "estimate_flow": {
            "active": active,
            "version": 1,
            "pending_step_index": pending_step_index,
            "draft": draft,
        }
    }


def _prompt_for_pending_index(idx: int) -> str:
    step = _STEPS[idx]
    if step == "product_details":
        return (
            "Got it — I’ll collect a structured Quick Ship estimate.\n\n"
            "**Step 1/5:** In one line send **quantity**, **size (W×H in.)**, **material**, "
            "**finish**, and **print colors**."
        )
    if step == "business_name":
        return "**Step 2/5:** What **business name** should appear on the quote? Reply **Individual** if personal."
    if step == "contact_name":
        return "**Step 3/5:** What’s the **contact name** for this order?"
    if step == "email":
        return "**Step 4/5:** What **email** should we use for your quote link?"
    if step == "address":
        return "**Step 5/5:** **Shipping/billing address** (one line), or reply **skip**."
    return ""


def _persist_structured_estimate(
    *,
    conversation_id: str,
    draft: dict[str, Any],
) -> tuple[str | None, str | None]:
    blob = draft.get("product_details") or ""
    pricing_rows = retrieve_pricing(str(blob), limit=5)
    if not pricing_rows:
        return (
            "Everything’s captured, but the catalog lookup returned no rows yet. Reply again with qty + "
            "**exact WxH inches** + material/finish so we can latch a SKU.",
            None,
        )

    row = pricing_rows[0]
    qty_hint = draft.get("_quantity_hint")
    qty = int(qty_hint) if isinstance(qty_hint, int) else _extract_quantity(str(blob).lower())
    qty = qty or 1000

    ck = _qty_to_cost_key(qty)
    raw_cost = row.get(ck)
    if raw_cost is None:
        for alt in ("cost_1000", "cost_500", "cost_250", "cost_100"):
            if row.get(alt) is not None:
                ck = alt
                raw_cost = row.get(alt)
                break
    try:
        total_val = Decimal(str(raw_cost))
    except (InvalidOperation, TypeError, ValueError):
        return ("Could not read a catalog total — please revisit quantity/tier selections.", None)

    if total_val <= 0:
        return ("Catalog returned a zero/blank total — double-check qty vs Quick Ship breakpoints.", None)

    unit_val = total_val / Decimal(int(qty))
    sku = (str(row.get("sku") or "").strip()) or "CATALOG_ROW"
    desc_parts = [
        str(row.get("description") or "").strip(),
        str(draft.get("product_details") or "").strip(),
    ]
    desc = " | ".join(p for p in desc_parts if p)[:650]

    items = [
        {
            "sku": sku[:240],
            "description": desc,
            "quantity": float(qty),
            "unit_price": float(unit_val.quantize(Decimal("0.0001"))),
            "total": float(total_val.quantize(Decimal("0.01"))),
        }
    ]

    from fortis_cs_agent.tools import insert_fortis_estimate

    try:
        result = insert_fortis_estimate(
            conversation_id,
            str(draft.get("business_name") or ""),
            str(draft.get("contact_name") or ""),
            str(draft.get("email") or ""),
            "",
            str(draft.get("address") or ""),
            items,
            _DEFAULT_NOTES,
        )
    except Exception:
        logger.exception("estimate_flow insert failed cid=%s", conversation_id)
        return ("Estimate save failed on our side — try again shortly.", None)

    eid = str(result.get("estimate_id", "") or "").strip()
    link = _quote_absolute_url(eid)
    friendly = (
        "Estimate locked in.\n\n"
        f"**View quote:** {link}\n"
        f"Quote ID `{eid}`\n\n"
        "Questions? Just reply here."
    )
    return friendly, eid


@dataclass(frozen=True)
class EstimateFlowResult:
    handled: bool
    reply: str = ""
    assistant_meta: dict[str, Any] | None = None
    estimate_id: str | None = None


def handle_estimate_flow(
    *,
    user_message: str,
    conversation_history: list[dict[str, Any]],
    conversation_id: str,
) -> EstimateFlowResult:
    """Deterministic quoting wizard. Fallback to Grok via ``EstimateFlowResult(handled=False)``."""

    raw = (user_message or "").strip()
    req = is_estimate_request(raw)
    snap = latest_estimate_flow_snapshot(conversation_history)
    carrying = snap is not None

    if not req and not carrying:
        return EstimateFlowResult(handled=False)

    msg_lower = raw.lower()

    if req and carrying:
        snap = None
        carrying = False

    if not carrying:
        draft: dict[str, Any] = {}
        rich = _extract_quantity(msg_lower) is not None and _extract_dimensions(msg_lower)
        if rich:
            draft["product_details"] = raw
            qh = _extract_quantity(msg_lower)
            if qh:
                draft["_quantity_hint"] = qh
            reply = _prompt_for_pending_index(1)
            return EstimateFlowResult(
                handled=True,
                reply=reply,
                assistant_meta=_meta_payload(draft, pending_step_index=1),
                estimate_id=None,
            )

        reply = _prompt_for_pending_index(0)
        return EstimateFlowResult(
            handled=True,
            reply=reply,
            assistant_meta=_meta_payload({}, pending_step_index=0),
            estimate_id=None,
        )

    assert snap is not None
    draft, pending_idx = snap
    draft = dict(draft)
    pending_idx = max(0, min(pending_idx, len(_STEPS) - 1))
    step_key = _STEPS[pending_idx]

    if msg_lower in {"cancel", "stop", "never mind"} and pending_idx <= 2:
        return EstimateFlowResult(
            handled=True,
            reply="Okay — leaving the scripted estimate flow. Ask anything else!",
            assistant_meta={"estimate_flow": {"active": False, "version": 1}},
        )

    answer = raw.strip()

    if step_key == "product_details":
        draft["product_details"] = answer
        q = _extract_quantity(answer.lower())
        if q:
            draft["_quantity_hint"] = q
    elif step_key == "business_name":
        draft["business_name"] = answer
    elif step_key == "contact_name":
        draft["contact_name"] = answer
    elif step_key == "email":
        if not _looks_like_email(answer):
            return EstimateFlowResult(
                handled=True,
                reply="That email doesn’t look complete — try **someone@yourcompany.com**.",
                assistant_meta=_meta_payload(draft, pending_idx),
            )
        draft["email"] = answer
    elif step_key == "address":
        draft["address"] = "" if msg_lower == "skip" else answer
    next_idx = pending_idx + 1

    if next_idx >= len(_STEPS):
        friendly, estimate_id = _persist_structured_estimate(conversation_id=conversation_id, draft=draft)
        return EstimateFlowResult(
            handled=True,
            reply=friendly or "",
            assistant_meta={"estimate_flow": {"active": False, "version": 1, "completed": True}},
            estimate_id=estimate_id,
        )

    reply = _prompt_for_pending_index(next_idx)
    return EstimateFlowResult(
        handled=True,
        reply=reply,
        assistant_meta=_meta_payload(draft, pending_step_index=next_idx),
        estimate_id=None,
    )


def try_handle_estimate_flow(**kwargs: Any) -> EstimateFlowResult:
    return handle_estimate_flow(**kwargs)
