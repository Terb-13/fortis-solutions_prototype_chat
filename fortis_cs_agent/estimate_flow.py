"""
Deterministic Estimate Mode: capture quote fields sequentially, optionally bypassing Grok.

State persists on the assistant message ``meta["estimate_flow"]``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from fortis_cs_agent.estimate_detector import is_estimate_request
from fortis_cs_agent.knowledge import retrieve_pricing
from fortis_cs_agent.tools import execute_agent_tool

logger = logging.getLogger(__name__)

# Bumped when changing estimate wizard behavior; exposed on GET /health for deploy verification.
ESTIMATE_FLOW_BUILD = "wizard-v5-partial-step1-meta-fallback"

_STEPS = ("product_details", "business_name", "contact_name", "email", "address")
_DEFAULT_NOTES = "This quote does not include shipping or taxes. Prices are valid for 30 days."

_RESTART_FLOW_RE = re.compile(
    r"\b(?:start\s+over|restart|begin\s+again|new\s+(?:quote|estimate)|"
    r"another\s+(?:quote|estimate)|different\s+(?:quote|estimate)|cancel\s+(?:the\s+)?(?:quote|estimate))\b",
    re.IGNORECASE,
)


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
    return (
        re.search(
            r"(\d+(?:\.\d+)?)\s*(?:x|×|\bby\b)\s*(\d+(?:\.\d+)?)",
            text_lower,
            re.IGNORECASE,
        )
        is not None
    )


def _coerce_meta_outer(raw: Any) -> dict[str, Any]:
    """PostgREST / drivers may return ``meta`` as a JSON string; normalize to dict."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            val = json.loads(raw)
            return val if isinstance(val, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


_STEP_HEADER_RE = re.compile(r"\*\*Step\s+(\d)/5\*\*|Step\s+(\d)/5\s*:", re.IGNORECASE)
_STEP1_MATERIAL_RE = re.compile(
    r"\b(bopp|pet|vinyl|vynil|paper|poly|polyprop|polypropylene|\bpp\b|\bpe\b|film|foil|kimdura|tyvek)\b",
    re.IGNORECASE,
)
_STEP1_FINISH_RE = re.compile(
    r"\b(gloss|matte|\bmat\b|satin|soft[\s-]?touch|uv\b|lam|laminated|varnish)\b",
    re.IGNORECASE,
)
_STEP1_COLORS_RE = re.compile(
    r"\b(cmyk|process|4[\s/]*c|four[\s-]?color|full[\s-]?col|spot|1[\s-]?col|one[\s-]?color|\bpms\b|pantone)\b",
    re.IGNORECASE,
)


def _step1_missing_parts(low: str) -> list[str]:
    miss: list[str] = []
    if _extract_quantity(low) is None:
        miss.append("**quantity**")
    if not _extract_dimensions(low):
        miss.append("**size (W×H in.)**")
    if not _STEP1_MATERIAL_RE.search(low):
        miss.append("**material** (e.g. white BOPP)")
    if not _STEP1_FINISH_RE.search(low):
        miss.append("**finish** (e.g. gloss)")
    if not _STEP1_COLORS_RE.search(low):
        miss.append("**print colors** (e.g. CMYK)")
    return miss


def _step1_specs_complete(low: str) -> bool:
    return not _step1_missing_parts(low)


def _merge_step1_blob(prior: str, answer: str) -> str:
    p, a = prior.strip(), answer.strip()
    if not p:
        return a
    if not a:
        return p
    if a.lower() in p.lower():
        return p
    if p.lower() in a.lower():
        return a
    return f"{p}; {a}"


def _infer_snapshot_from_assistant_content(
    history_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], int] | None:
    """If ``meta`` was dropped (e.g. JSON string not parsed client-side), recover step from assistant copy."""
    for row in reversed(history_rows):
        if row.get("role") != "assistant":
            continue
        content = row.get("content") or ""
        m = _STEP_HEADER_RE.search(content)
        if not m:
            continue
        n = int(m.group(1) or m.group(2))
        idx = max(0, min(n - 1, len(_STEPS) - 1))
        meta = _coerce_meta_outer(row.get("meta"))
        block = meta.get("estimate_flow")
        if isinstance(block, dict) and block.get("completed") is True:
            continue
        logger.info(
            "estimate_flow inferred step_index=%s from assistant content (meta missing or inactive on latest rows)",
            idx,
        )
        return {}, idx
    return None


def _looks_like_email(s: str) -> bool:
    s = (s or "").strip()
    return "@" in s and "." in s.split("@", 1)[-1]


def _quote_absolute_url(estimate_id: str) -> str:
    pub = (
        os.getenv("PUBLIC_BASE_URL")
        or os.getenv("NEXT_PUBLIC_QUOTE_SITE_URL")
        or ""
    ).strip().rstrip("/")
    tail = f"/quote/{estimate_id}"
    return f"{pub}{tail}" if pub else tail


def _cost_key_human_label(cost_key: str) -> str:
    tier = (
        ("cost_100", "100-label"),
        ("cost_250", "250-label"),
        ("cost_500", "500-label"),
        ("cost_1000", "1,000-label"),
        ("cost_1500", "1,500-label"),
        ("cost_2000", "2,000-label"),
        ("cost_2500", "2,500-label"),
        ("cost_3000", "3,000-label"),
        ("cost_4000", "4,000-label"),
        ("cost_5000", "5,000-label"),
    )
    for key, lab in tier:
        if cost_key == key:
            return lab
    return cost_key.replace("cost_", "").replace("_", " ")


def _catalog_match_sentence(row: dict[str, Any]) -> str | None:
    tag = str(row.get("_fortis_pricing_match") or "").strip()
    if tag in ("closest_size", "closest_fallback"):
        return (
            "**Closest match:** This total is driven by the **nearest available Quick Ship line** "
            "in our pricing grid for your quantity and specs. Confirm the SKU and description on your quote page "
            "before committing to production."
        )
    return None


def latest_estimate_flow_snapshot(history_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], int] | None:
    """Return draft + pending step index (which field THIS user reply should fill)."""
    for row in reversed(history_rows):
        if row.get("role") != "assistant":
            continue
        meta_outer = _coerce_meta_outer(row.get("meta"))
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
    return _infer_snapshot_from_assistant_content(history_rows)


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
            "Everything’s captured, but the catalog lookup returned no rows yet.\n\n"
            "• Send one line again: **qty, WxH inches, material/finish/colors**\n\n"
            "If this keeps happening, call **GET /health** on the API and check **pricing_health** — "
            "`has_rows` should be true. If it is false, the Quick Ship pricing table is empty, the table name "
            "does not match (set env **FORTIS_PRICING_TABLE**), or this deploy’s Supabase key cannot read that table.",
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
        str(row.get("comment_application") or row.get("description") or "").strip(),
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

    try:
        out = execute_agent_tool(
            "create_estimate",
            {
                "business_name": str(draft.get("business_name") or ""),
                "contact_name": str(draft.get("contact_name") or ""),
                "email": str(draft.get("email") or ""),
                "phone": "",
                "address": str(draft.get("address") or ""),
                "items": items,
                "notes": _DEFAULT_NOTES,
            },
            persist_estimate=True,
            conversation_id=conversation_id,
        )
    except Exception:
        logger.exception("estimate_flow create_estimate failed cid=%s", conversation_id)
        return ("Estimate save failed on our side — try again shortly.", None)

    if isinstance(out, dict) and out.get("error"):
        hint = str(out.get("message") or out.get("hint") or "unknown error")[:500]
        logger.warning("estimate_flow create_estimate tool error cid=%s detail=%s", conversation_id, hint)
        return (f"Estimate save failed — {hint}", None)

    eid = str((out or {}).get("estimate_id", "") or "").strip()
    if not eid:
        return ("Estimate save failed — no estimate id returned.", None)
    link = _quote_absolute_url(eid)
    mat = str(row.get("material") or "").strip()
    fin = str(row.get("finish") or "").strip()
    if mat and fin:
        mat_fin = f"{mat}, {fin}"
    elif mat:
        mat_fin = mat
    elif fin:
        mat_fin = fin
    else:
        mat_fin = "See catalog row on quote"

    total_str = f"${float(total_val):,.2f}"
    unit_str = f"${float(unit_val.quantize(Decimal('0.0001'))):,.4f}"
    qty_str = f"{int(qty):,}"

    tier_line = (
        f"Catalog pricing tier applied: **{_cost_key_human_label(ck)}** bracket (matched to your requested quantity)."
    )

    match_txt = _catalog_match_sentence(row)
    lines_note = f"\n\n{match_txt}" if match_txt else ""

    friendly = (
        "Thank you — your structured **Quick Ship** estimate has been saved. "
        "Below is a concise pricing summary matched to our Quick Ship pricing grid.\n\n"
        "### Pricing summary\n"
        f"- **SKU:** `{sku}`\n"
        f"- **Quantity:** {qty_str} labels\n"
        f"- **Unit price:** {unit_str} per label (extended total shown below divided by qty)\n"
        f"- **Line total:** {total_str}\n"
        f"- **Material / finish (catalog row):** {mat_fin}\n"
        f"- {tier_line}"
        f"{lines_note}"
        "\n\n### Your quote\n"
        f"Here's your quote: {link}\n\n"
        f"Quote reference · `{eid}`\n\n"
        "**Terms:** Quote valid **30 days**; does **not** include shipping or taxes (same notes appear on your quote page).\n\n"
        "If anything looks off versus what you typed, reply here before you place the order—we’re happy to double-check "
        "the nearest catalog line or walk you through the next steps."
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

    # Never treat keyword "quote" inside answers (e.g. company names) as a reason to reset the wizard.
    if carrying and _RESTART_FLOW_RE.search(raw):
        return EstimateFlowResult(
            handled=True,
            reply=_prompt_for_pending_index(0),
            assistant_meta=_meta_payload({}, pending_step_index=0),
            estimate_id=None,
        )

    if not carrying:
        draft = {}
        rich = _extract_quantity(msg_lower) is not None and _extract_dimensions(msg_lower)
        if rich:
            draft["product_details"] = raw
            qh = _extract_quantity(msg_lower)
            if qh:
                draft["_quantity_hint"] = qh
            if _step1_specs_complete(msg_lower):
                reply = _prompt_for_pending_index(1)
                return EstimateFlowResult(
                    handled=True,
                    reply=reply,
                    assistant_meta=_meta_payload(draft, pending_step_index=1),
                    estimate_id=None,
                )
            miss_txt = ", ".join(_step1_missing_parts(msg_lower))
            return EstimateFlowResult(
                handled=True,
                reply=(
                    f"**Step 1/5:** I’ve captured: **{raw}**\n\n"
                    f"Still need: {miss_txt}.\n\n"
                    "Send the missing pieces in another line if that’s easier—I’ll combine them."
                ),
                assistant_meta=_meta_payload(draft, pending_step_index=0),
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

    if pending_idx >= 1 and not str(draft.get("product_details") or "").strip():
        logger.warning(
            "estimate_flow reset: pending_step_index=%s but product_details empty cid=%s",
            pending_idx,
            conversation_id,
        )
        return EstimateFlowResult(
            handled=True,
            reply=_prompt_for_pending_index(0),
            assistant_meta=_meta_payload({}, pending_step_index=0),
            estimate_id=None,
        )

    step_key = _STEPS[pending_idx]

    if msg_lower in {"cancel", "stop", "never mind"} and pending_idx <= 2:
        return EstimateFlowResult(
            handled=True,
            reply="Okay — leaving the scripted estimate flow. Ask anything else!",
            assistant_meta={"estimate_flow": {"active": False, "version": 1}},
        )

    answer = raw.strip()

    if step_key == "product_details":
        prior = str(draft.get("product_details") or "").strip()
        merged = _merge_step1_blob(prior, answer)
        draft["product_details"] = merged
        low = merged.lower()
        q = _extract_quantity(low)
        if q:
            draft["_quantity_hint"] = q
        if not _step1_specs_complete(low):
            miss_txt = ", ".join(_step1_missing_parts(low))
            return EstimateFlowResult(
                handled=True,
                reply=(
                    f"**Step 1/5:** Here’s what I have so far: **{merged}**\n\n"
                    f"Still need: {miss_txt}.\n\n"
                    "Add the rest in one line or another short line—I’ll keep combining."
                ),
                assistant_meta=_meta_payload(draft, pending_step_index=0),
                estimate_id=None,
            )
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
