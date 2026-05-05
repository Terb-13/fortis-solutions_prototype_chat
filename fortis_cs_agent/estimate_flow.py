"""
Deterministic Estimate Mode: capture quote fields sequentially, optionally bypassing Grok.

State persists on the assistant message ``meta["estimate_flow"]``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from fortis_cs_agent.estimate_detector import is_estimate_request, should_skip_estimate_wizard_opener, shopper_utterance_for_estimate_heuristics
from fortis_cs_agent.knowledge import retrieve_pricing
from fortis_cs_agent.tools import execute_agent_tool

logger = logging.getLogger(__name__)

# Bumped when changing estimate wizard behavior; exposed on GET /health for deploy verification.
ESTIMATE_FLOW_BUILD = "wizard-v9-cold-opener-guard"

_STEPS = ("product_details", "business_name", "contact_name", "email", "address")
_DEFAULT_NOTES = "This quote does not include shipping or taxes. Prices are valid for 30 days."

_RESTART_FLOW_RE = re.compile(
    r"\b(?:start\s+over|restart|begin\s+again|new\s+(?:quote|estimate)|"
    r"another\s+(?:quote|estimate)|different\s+(?:quote|estimate)|cancel\s+(?:the\s+)?(?:quote|estimate))\b",
    re.IGNORECASE,
)

# Cold “Got it — Step 1/5” intro only when language clearly asks for pricing (and not blocked by should_skip).
_EXPLICIT_QUOTE_COLD_OPENER_RE = re.compile(
    r"(?i)"
    r"\bpricing\b|\bprices?\b|\bhow\s+much\b"
    r"|\bcreate\b.{0,24}\bestimates?\b|\bquick\s+ship\b"
    r"|\bcan\s+you\s+quote\b|\b(get|need)\s+(an?\s+)?(quote|estimate|price)\b"
    r"|\bfor\s+a\s+quote\b|\bballpark\b|\bprice\s+quote\b"
    r"|(?<!\bdont\s)(?<!\bdon't\s)\bwant\s+(?:an?\s+)?(?:quote|estimate)\b"
    r"|\b(?:give|send)\s+(?:me\s+)?(?:an?\s+)?(?:quote|estimate|price)\b"
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
            r"(\d+(?:\.\d+)?)\s*(?:x|×|✕|╳|\*|by)\s*(\d+(?:\.\d+)?)",
            text_lower,
            re.IGNORECASE,
        )
        is not None
    )


def _normalize_user_line_for_wizard(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).strip()
    t = re.sub(r"[\u200b-\u200d\ufeff]", "", t)
    t = t.replace("\u00a0", " ")
    t = (
        t.replace("×", "x")
        .replace("✕", "x")
        .replace("╳", "x")
        .replace("∗", "*")
        .replace("＊", "*")
    )
    return t.strip()


def _has_step1_product_signals(low: str) -> bool:
    """True when the line looks like label specs even if strict rich/qty×dim didn’t both parse."""
    return bool(
        _extract_quantity(low) is not None
        or _extract_dimensions(low)
        or _STEP1_MATERIAL_RE.search(low)
        or _STEP1_FINISH_RE.search(low)
        or _STEP1_COLORS_RE.search(low)
        or re.search(r"\b(labels?|stickers?)\b", low)
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


def _wizard_quote_finished_in_history(history_rows: list[dict[str, Any]]) -> bool:
    for row in reversed(history_rows):
        if row.get("role") != "assistant":
            continue
        c = row.get("content") or ""
        if "### Pricing summary" in c or "Quick Ship** estimate has been saved" in c:
            return True
        meta = _coerce_meta_outer(row.get("meta"))
        block = meta.get("estimate_flow")
        if isinstance(block, dict) and block.get("completed") is True:
            return True
    return False


def _first_pending_step_index(draft: dict[str, Any]) -> int:
    blob = str(draft.get("product_details") or "").lower()
    if not _step1_specs_complete(blob):
        return 0
    if not str(draft.get("business_name") or "").strip():
        return 1
    if not str(draft.get("contact_name") or "").strip():
        return 2
    if not str(draft.get("email") or "").strip():
        return 3
    if not str(draft.get("address") or "").strip():
        return 4
    return len(_STEPS)


def _looks_like_labeled_estimate_form(text: str) -> bool:
    return bool(
        re.search(
            r"(?i)(quantity|qty|material|finish|print\s*colors|colors|business\s*name|company|contact\s*name|\bcontact\b|email|shipping|address)\s*[:=]",
            text,
        )
    )


def _parse_labeled_estimate_to_draft(text: str) -> dict[str, Any]:
    """Parse 'quantity: 500, material: ... business name: ...' style blobs into draft fields."""
    out: dict[str, Any] = {}
    t = text.strip()

    def grab(rx: str) -> str | None:
        m = re.search(rx, t, flags=re.IGNORECASE)
        return m.group(1).strip().rstrip(" ,") if m else None

    parts: list[str] = []
    q = grab(r"(?:quantity|qty)\s*[:=]\s*(\d+)")
    if q:
        parts.append(f"{q} labels")
    dim_m = re.search(r"(\d+\.?\d*)\s*[x×]\s*(\d+\.?\d*)", t.lower())
    if dim_m:
        parts.append(f"{dim_m.group(1)}x{dim_m.group(2)} in")
    mat = grab(r"material\s*[:=]\s*([^,\n]+)")
    if mat:
        parts.append(mat)
    fin = grab(r"finish\s*[:=]\s*([^,\n]+)")
    if fin:
        parts.append(fin)
    pc = grab(r"print\s*colors\s*[:=]\s*([^,\n]+)") or grab(r"colors\s*[:=]\s*([^,\n]+)")
    if pc:
        parts.append(pc)
    blob = "; ".join(p for p in parts if p)
    if blob:
        out["product_details"] = blob
        q2 = _extract_quantity(blob.lower())
        if q2:
            out["_quantity_hint"] = q2

    bn = grab(r"business\s*name\s*[:=]\s*([^,\n]+)") or grab(r"company\s*[:=]\s*([^,\n]+)")
    if bn:
        out["business_name"] = bn
    cn = grab(r"contact\s*name\s*[:=]\s*([^,\n]+)")
    if cn:
        out["contact_name"] = cn
    if not out.get("contact_name"):
        c_alt = grab(r"(?<![\w])contact\s*[:=]\s*([^,\n]+)")
        if c_alt and "@" not in c_alt:
            out["contact_name"] = c_alt
    em = grab(r"email\s*[:=]\s*(\S+@\S+)")
    if em and _looks_like_email(em):
        out["email"] = em
    addr = grab(r"(?:shipping(?:/billing)?\s*address|billing\s*address)\s*[:=]\s*([^,\n]+)")
    if not addr:
        addr = grab(r"(?<![\w])address\s*[:=]\s*([^,\n]+)")
    if addr:
        out["address"] = addr
    return out


def _recover_snapshot_after_step1_prompt(
    history_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], int] | None:
    """Rebuild draft when ``meta`` was lost but Step 1/5 ran and users replied (e.g. Grok interleaved)."""
    if not history_rows or _wizard_quote_finished_in_history(history_rows):
        return None
    idx_step1: int | None = None
    for i, row in enumerate(history_rows):
        if row.get("role") == "assistant" and "Step 1/5" in (row.get("content") or ""):
            idx_step1 = i
    if idx_step1 is None:
        return None
    row = history_rows[idx_step1]
    meta = _coerce_meta_outer(row.get("meta"))
    block = meta.get("estimate_flow")
    draft: dict[str, Any] = {}
    if isinstance(block, dict):
        d0 = block.get("draft")
        if isinstance(d0, dict):
            draft = dict(d0)
    for urow in history_rows[idx_step1 + 1 :]:
        if urow.get("role") != "user":
            continue
        u = (urow.get("content") or "").strip()
        if not u:
            continue
        if _looks_like_labeled_estimate_form(u):
            patches = _parse_labeled_estimate_to_draft(u)
            for k, v in patches.items():
                if v is None or (isinstance(v, str) and not v.strip()):
                    continue
                if k == "product_details" and draft.get("product_details"):
                    draft["product_details"] = _merge_step1_blob(str(draft["product_details"]), str(v))
                else:
                    draft[k] = v
        else:
            prior = str(draft.get("product_details") or "").strip()
            draft["product_details"] = _merge_step1_blob(prior, u)
        low = str(draft.get("product_details") or "").lower()
        qn = _extract_quantity(low)
        if qn:
            draft["_quantity_hint"] = qn
    pending = _first_pending_step_index(draft)
    logger.info(
        "estimate_flow recovered draft from history after Step 1/5 (pending_step_index=%s keys=%s)",
        pending,
        list(draft.keys()),
    )
    return draft, pending


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
    rec = _recover_snapshot_after_step1_prompt(history_rows)
    if rec is not None:
        return rec
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

    raw = _normalize_user_line_for_wizard(shopper_utterance_for_estimate_heuristics(user_message))
    if not raw:
        return EstimateFlowResult(handled=False)

    snap = latest_estimate_flow_snapshot(conversation_history)
    carrying = snap is not None

    # Hard guard: never open the wizard on capability / refusal / SBU / general-topic openers.
    if not carrying and should_skip_estimate_wizard_opener(raw):
        return EstimateFlowResult(handled=False)

    req = is_estimate_request(raw)

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
        if req and _looks_like_labeled_estimate_form(raw):
            labeled_draft = _parse_labeled_estimate_to_draft(raw)
            if labeled_draft:
                pi = _first_pending_step_index(labeled_draft)
                if pi >= len(_STEPS):
                    friendly, estimate_id = _persist_structured_estimate(
                        conversation_id=conversation_id, draft=dict(labeled_draft)
                    )
                    return EstimateFlowResult(
                        handled=True,
                        reply=friendly or "",
                        assistant_meta={"estimate_flow": {"active": False, "version": 1, "completed": True}},
                        estimate_id=estimate_id,
                    )
                blob_low = str(labeled_draft.get("product_details") or "").lower()
                if pi == 0 and not _step1_specs_complete(blob_low):
                    miss_txt = ", ".join(_step1_missing_parts(blob_low))
                    return EstimateFlowResult(
                        handled=True,
                        reply=(
                            f"**Step 1/5:** I’ve captured: **{labeled_draft.get('product_details', '')}**\n\n"
                            f"Still need: {miss_txt}.\n\n"
                            "Send the missing pieces—you can add another line and I’ll combine."
                        ),
                        assistant_meta=_meta_payload(dict(labeled_draft), 0),
                        estimate_id=None,
                    )
                return EstimateFlowResult(
                    handled=True,
                    reply=_prompt_for_pending_index(pi),
                    assistant_meta=_meta_payload(dict(labeled_draft), pi),
                    estimate_id=None,
                )

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

        if not rich:
            if should_skip_estimate_wizard_opener(raw):
                return EstimateFlowResult(handled=False)
            if _has_step1_product_signals(msg_lower):
                draft["product_details"] = raw
                qh = _extract_quantity(msg_lower)
                if qh:
                    draft["_quantity_hint"] = qh
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
            if not _EXPLICIT_QUOTE_COLD_OPENER_RE.search(msg_lower):
                return EstimateFlowResult(handled=False)

        reply = _prompt_for_pending_index(0)
        return EstimateFlowResult(
            handled=True,
            reply=reply,
            assistant_meta=_meta_payload({}, pending_step_index=0),
            estimate_id=None,
        )

    assert snap is not None
    draft, _snap_pending = snap
    draft = dict(draft)
    fp0 = _first_pending_step_index(draft)
    if fp0 >= len(_STEPS):
        friendly, estimate_id = _persist_structured_estimate(conversation_id=conversation_id, draft=draft)
        return EstimateFlowResult(
            handled=True,
            reply=friendly or "",
            assistant_meta={"estimate_flow": {"active": False, "version": 1, "completed": True}},
            estimate_id=estimate_id,
        )
    pending_idx = max(0, min(fp0, len(_STEPS) - 1))

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
        if _looks_like_labeled_estimate_form(raw):
            patches = _parse_labeled_estimate_to_draft(raw)
            merged_pd = str(draft.get("product_details") or "").strip()
            for k, v in patches.items():
                if v is None or (isinstance(v, str) and not str(v).strip()):
                    continue
                if k == "product_details":
                    merged_pd = _merge_step1_blob(merged_pd, str(v))
                else:
                    draft[k] = v
            if merged_pd:
                draft["product_details"] = merged_pd
            low = str(draft.get("product_details") or "").lower()
            q = _extract_quantity(low)
            if q:
                draft["_quantity_hint"] = q
            fp2 = _first_pending_step_index(draft)
            if fp2 == 0 and not _step1_specs_complete(low):
                miss_txt = ", ".join(_step1_missing_parts(low))
                return EstimateFlowResult(
                    handled=True,
                    reply=(
                        f"**Step 1/5:** Here’s what I have so far: **{draft['product_details']}**\n\n"
                        f"Still need: {miss_txt}.\n\n"
                        "Add the rest in one line or another short line—I’ll keep combining."
                    ),
                    assistant_meta=_meta_payload(draft, pending_step_index=0),
                    estimate_id=None,
                )
            if fp2 > 0:
                return EstimateFlowResult(
                    handled=True,
                    reply=_prompt_for_pending_index(fp2),
                    assistant_meta=_meta_payload(draft, pending_step_index=fp2),
                    estimate_id=None,
                )
            # fp2==0 and step 1 complete — fall through to next_idx below
        else:
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
