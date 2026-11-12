"""
Deterministic Estimate Mode: capture quote fields sequentially, optionally bypassing Grok.

State persists in Supabase ``fortis_estimate_sessions`` (per conversation) and is mirrored on
the assistant message ``meta["estimate_flow"]`` for the UI.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from fortis_cs_agent.estimate_detector import (
    is_estimate_request,
    should_exit_estimate_wizard_for_topic_shift,
    should_resume_estimate_wizard_from_paused,
    should_skip_estimate_wizard_opener,
    shopper_utterance_for_estimate_heuristics,
)
from fortis_cs_agent.estimate_sessions import (
    EstimateSessionStatus,
    fetch_estimate_session,
    update_estimate_session_status,
    upsert_estimate_session,
)
from fortis_cs_agent.knowledge import _extract_quantity, _extract_size_tuple, retrieve_pricing
from fortis_cs_agent.tools import execute_agent_tool, quote_page_url

logger = logging.getLogger(__name__)

# Bumped when changing estimate wizard behavior; exposed on GET /health for deploy verification.
ESTIMATE_FLOW_BUILD = "wizard-v19-size-change-bugbot-fixes"

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


def _qty_to_cost_key(qty: int) -> str | None:
    """Map a quantity onto a real bracket. Below 100 there is no tier."""
    if qty < 100:
        return None
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


def _stored_collected_to_draft(data: dict[str, Any]) -> dict[str, Any]:
    """Rebuild internal wizard draft from JSONB ``collected_data``."""
    draft: dict[str, Any] = {}
    pd = data.get("product_details")
    if pd and str(pd).strip():
        draft["product_details"] = str(pd).strip()
    else:
        parts: list[str] = []
        if data.get("quantity") is not None:
            try:
                parts.append(f"{int(data['quantity'])} labels")
            except (TypeError, ValueError):
                pass
        sz = data.get("size")
        if sz and str(sz).strip():
            s = str(sz).strip()
            low_s = s.lower()
            if re.search(r"\d", s) and "x" in low_s and "in" not in low_s and not low_s.endswith("labels"):
                parts.append(f"{s} in")
            else:
                parts.append(s)
        for key in ("material", "finish", "colors"):
            v = data.get(key)
            if v and str(v).strip():
                parts.append(str(v).strip())
        if parts:
            draft["product_details"] = "; ".join(parts)
    qh = data.get("_quantity_hint")
    if qh is not None:
        try:
            draft["_quantity_hint"] = int(qh)
        except (TypeError, ValueError):
            pass
    elif data.get("quantity") is not None:
        try:
            draft["_quantity_hint"] = int(data["quantity"])
        except (TypeError, ValueError):
            pass
    for k in ("business_name", "contact_name", "email", "address"):
        v = data.get(k)
        if v is not None and str(v).strip():
            draft[k] = str(v).strip()
    return draft


def _draft_to_stored_collected(draft: dict[str, Any]) -> dict[str, Any]:
    """Flatten internal draft to JSONB with structured label fields plus product_details blob."""
    blob = str(draft.get("product_details") or "")
    low = blob.lower()
    dim_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:x|×|✕|╳)\s*(\d+(?:\.\d+)?)", low, re.IGNORECASE)
    mat_m = _STEP1_MATERIAL_RE.search(low)
    fin_m = _STEP1_FINISH_RE.search(low)
    col_m = _STEP1_COLORS_RE.search(low)
    return {
        "quantity": _extract_quantity(low),
        "size": f"{dim_m.group(1)}x{dim_m.group(2)}" if dim_m else None,
        "material": mat_m.group(0).strip() if mat_m else None,
        "finish": fin_m.group(0).strip() if fin_m else None,
        "colors": col_m.group(0).strip() if col_m else None,
        "business_name": (str(draft["business_name"]).strip() if draft.get("business_name") else None),
        "contact_name": (str(draft["contact_name"]).strip() if draft.get("contact_name") else None),
        "email": (str(draft["email"]).strip() if draft.get("email") else None),
        "address": (str(draft["address"]).strip() if draft.get("address") else None),
        "product_details": blob.strip() or None,
        "_quantity_hint": draft.get("_quantity_hint"),
    }


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
        if (
            "Your quote is ready." in c
            or "couldn't price that one automatically" in c
            or "### Pricing summary" in c
            or "Quick Ship** estimate has been saved" in c
        ):
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
        raw_u = (urow.get("content") or "").strip()
        if not raw_u:
            continue
        u = shopper_utterance_for_estimate_heuristics(raw_u)
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


_EMAIL_SYNTAX_RE = re.compile(
    r"^[a-zA-Z0-9][a-zA-Z0-9._%+\-]*@[a-zA-Z0-9][a-zA-Z0-9.\-]*\.[a-zA-Z]{2,}$"
)

# Whole-message patterns that are not usable legal / DBA business names.
_GIBBERISH_OR_META_ANSWER_RE = re.compile(
    r"(?i)^\s*("
    r"wtf|lol|lmfao|rofl|idk|idc|bruh|meh|ugh|eh|huh|hmm+|nm|nvm|"
    r"n/?a|none|null|nothing|no|nah|nope|yep|yea|yeah|yes|sure|maybe|ok+|k\.?|skip|"
    r"asdf+|qwerty|foo|bar|xxx+|abc123|1234|test\s*123|"
    r"help|why|because|whatever|dunno|i\s*don'?t\s*know|guess|"
    r"idk\??|wth|ffs|omg"
    r")\s*[!?.]*\s*$"
)

# Contact name: obvious non-names (not exhaustive).
_GIBBERISH_CONTACT_RE = re.compile(
    r"(?i)^\s*("
    r"wtf|lol|asdf|foo|bar|xxx|test|none|n/?a|idk|nobody|someone|anyone|meh|"
    r"your\s+mom|admin|user|customer|sir|madam|dude|bro|"
    r"yes|yep|yea|yeah|no|nope|nah|ok+|sure|skip"
    r")\s*[!?.]*\s*$"
)



def _safe_ack_snippet(text: str, *, max_len: int = 72) -> str:
    """Short, safe excerpt of what the user typed for acknowledgments."""
    t = re.sub(r"[\x00-\x1f\x7f]", "", (text or "").strip())
    if not t:
        return "that"
    if len(t) > max_len:
        return t[: max_len - 1] + "…"
    return t


def _business_name_field_issue(answer: str) -> str | None:
    """
    None = acceptable. Otherwise a short code: empty, short, no_letters, gibberish, numbers_only.
    """
    raw = (answer or "").strip()
    if not raw:
        return "empty"
    low = raw.lower()
    if low in {"individual", "personal", "consumer", "self"}:
        return None
    if _extract_dimensions(low) or re.search(r"\b(core|requirements?|inch(es)?)\b", low):
        return "product_spec"
    if len(raw) < 3:
        return "short"
    if not re.search(r"[a-zA-Z]", raw):
        return "no_letters"
    if sum(ch.isdigit() for ch in raw) >= len(raw) - 1 and any(ch.isdigit() for ch in raw):
        return "numbers_only"
    if _GIBBERISH_OR_META_ANSWER_RE.match(raw):
        return "gibberish"
    letters = [ch for ch in raw.lower() if ch.isalpha()]
    if letters and len(set(letters)) == 1 and len(raw) <= 6:
        return "gibberish"
    return None


def _contact_name_field_issue(answer: str) -> str | None:
    """None = acceptable. Codes: empty, short, no_letters, gibberish, numbers_only."""
    raw = (answer or "").strip()
    if not raw:
        return "empty"
    low = raw.lower()
    if _extract_dimensions(low) or re.search(r"\b(core|requirements?|inch(es)?)\b", low):
        return "product_spec"
    if len(raw) < 2:
        return "short"
    if not re.search(r"[a-zA-Z]", raw):
        return "no_letters"
    if raw.isdigit():
        return "numbers_only"
    if _GIBBERISH_CONTACT_RE.match(raw):
        return "gibberish"
    return None


def _address_field_issue(answer: str) -> str | None:
    """None = ok. Codes: empty_not_skip, too_vague."""
    a = (answer or "").strip()
    if not a:
        return "empty_not_skip"
    low = a.lower()
    if low in {"n/a", "na", "none", "same", "tbd"} and len(a) < 12:
        return "too_vague"
    if len(a) < 8 and not re.search(r"\d", a) and "po box" not in low:
        return "too_vague"
    return None


_PRODUCT_SPEC_REASK = "That looks like a label spec, not a name. "


def _invalid_business_name_reply(code: str, snippet: str) -> str:
    ack = (
        f"You sent **{snippet}** — "
        if snippet and snippet != "that"
        else ""
    )
    if code == "empty":
        body = "I didn’t catch a **business name**. "
    elif code == "short":
        body = ack + "that’s a bit **too short** to print as the business on a quote. "
    elif code == "no_letters":
        body = ack + "I need at least some **letters** (a real company or brand name). "
    elif code == "numbers_only":
        body = ack + "a **business name** can’t be only numbers. "
    elif code == "product_spec":
        body = ack + _PRODUCT_SPEC_REASK
    else:
        body = ack + "that doesn’t read like a **company or organization name**. "

    return (
        f"{body}"
        "**Step 2/5 (again):** What business name should go on the quote? Reply Individual if it's personal."
    )


def _invalid_contact_name_reply(code: str, snippet: str) -> str:
    ack = f"You wrote **{snippet}** — " if snippet and snippet != "that" else ""
    if code == "empty":
        lead = "I need a **contact name** for this quote. "
    elif code == "short":
        lead = ack + "please give at least **two characters** (a first name or full contact name). "
    elif code == "no_letters":
        lead = ack + "I need a **person’s name** (letters), not only numbers or symbols. "
    elif code == "numbers_only":
        lead = ack + "the **contact** should be a person’s name, not only digits. "
    elif code == "product_spec":
        lead = ack + _PRODUCT_SPEC_REASK
    else:
        lead = ack + "that doesn’t look like someone’s **name** (first + last is ideal). "

    return (
        f"{lead}"
        "Who should we list as the day-to-day **contact**?\n\n"
        "**Step 3/5 (again):** What’s the **contact’s full name**?"
    )


def _invalid_email_reply(snippet: str) -> str:
    ack = f"I saw **{snippet}** — " if snippet and snippet != "that" else ""
    return (
        f"{ack}"
        "that doesn’t look like a complete **email address** "
        "(we need something in the form **someone@company.com** so we can send your quote link).\n\n"
        "**Step 4/5 (again):** Which **email** should we use?"
    )


def _invalid_address_reply(snippet: str) -> str:
    ack = f"You entered **{snippet}** — " if snippet and snippet != "that" else ""
    return (
        f"{ack}"
        "I need a bit more to ship or bill: please send at least one line with **street, city, state/postal** "
        "(or **P.O. Box** + city/state). If you truly don’t want to share an address yet, reply **skip**.\n\n"
        "**Step 5/5 (again):** **Shipping/billing address** (one line), or **skip**."
    )


def _looks_like_email(s: str) -> bool:
    s = (s or "").strip()
    if len(s) < 6 or "@" not in s:
        return False
    local, _, domain = s.partition("@")
    if not local or not domain or "." not in domain:
        return False
    if len(local) > 64 or len(domain) > 255:
        return False
    dom_main, _, tld = domain.rpartition(".")
    if len(tld) < 2 or not tld.isalpha():
        return False
    if ".." in s:
        return False
    return bool(_EMAIL_SYNTAX_RE.match(s))


def latest_estimate_flow_snapshot(history_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], int] | None:
    """Return draft + pending step index (which field THIS user reply should fill)."""
    # If a quote already completed in this thread, no carrying — earlier
    # mid-wizard assistant turns still have meta.estimate_flow.active=True
    # and would otherwise be picked up by the loop below as a stale snapshot.
    if _wizard_quote_finished_in_history(history_rows):
        return None
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


def _step1_continue_reply(miss_txt: str, history: list[dict[str, Any]] | None = None) -> str | None:
    """Step 1 partial progress. Each quantity wording is sent at most once."""
    reply = (
        f"**Step 1/5:** Thanks — I still need: {miss_txt}.\n\n"
        "Send what’s missing in one line (or another short line if that’s easier—I’ll combine them)."
    )
    if "**quantity**" not in miss_txt:
        return reply
    n = 0
    for row in history or []:
        if row.get("role") == "user" and _extract_quantity((row.get("content") or "").lower()):
            n = 0
            continue
        if row.get("role") != "assistant":
            continue
        text = (row.get("content") or "").strip()
        flow = _coerce_meta_outer(row.get("meta")).get("estimate_flow")
        if isinstance(flow, dict) and flow.get("active") is False:
            n = 0
            continue
        if text in (_QTY_THOUSAND, _QTY_LATER) or ("**quantity**" in text and "still need" in text.lower()):
            n += 1
    return [reply, _QTY_THOUSAND, _QTY_LATER][n] if n < 3 else None


def _resolve_wizard_snap(
    conversation_id: str,
    conversation_history: list[dict[str, Any]],
    session_row: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], int] | None:
    """Prefer Supabase session when ``in_progress``. ``paused`` does not re-attach the wizard from DB."""
    if session_row is None:
        session_row = fetch_estimate_session(conversation_id)
    if session_row and session_row.get("status") == "paused":
        return None
    if session_row and session_row.get("status") == "in_progress":
        cd = session_row.get("collected_data") or {}
        if not isinstance(cd, dict):
            cd = {}
        draft_db = _stored_collected_to_draft(cd)
        pending = _first_pending_step_index(draft_db)
        return draft_db, pending
    # Completed/abandoned sessions don't carry; don't fall through to history-
    # based recovery, which would otherwise return an earlier mid-wizard snapshot
    # and re-engage the wizard on a post-quote turn (e.g. "10% discount?").
    if session_row and session_row.get("status") in ("completed", "abandoned"):
        return None
    return latest_estimate_flow_snapshot(conversation_history)


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
            "Got it — I’ll collect a quote.\n\n"
            "**Step 1/5:** In one line send **quantity**, **size (W×H in.)**, **material**, "
            "**finish**, and **print colors**."
        )
    if step == "business_name":
        return "**Step 2/5:** What business name should go on the quote? Reply Individual if it's personal."
    if step == "contact_name":
        return "**Step 3/5:** What’s the **contact name** for this quote?"
    if step == "email":
        return "**Step 4/5:** What **email** should we use for your quote link?"
    if step == "address":
        return "**Step 5/5:** **Shipping/billing address** (one line), or reply **skip**."
    return ""


_PRICE_UNAVAILABLE = (
    "I couldn't price that one automatically. Double-check the size and quantity, "
    "or ask for a custom quote."
)
_QTY_THOUSAND = "No problem. Most orders start at 1,000 labels. Send a number to price it."
_QTY_LATER = "No problem. Whenever you have a number, send it here and I'll price it."
_CUSTOM_QUOTE_REPLY = "Sure. Send the size, quantity, material and finish you need."


def _step1_miss_result(
    draft: dict[str, Any], miss_txt: str, history: list[dict[str, Any]], conversation_id: str
) -> EstimateFlowResult:
    reply = _step1_continue_reply(miss_txt, history)
    if reply is None:
        upsert_estimate_session(
            conversation_id,
            current_step=1,
            collected_data=_draft_to_stored_collected(draft),
            status="in_progress",
        )
        return EstimateFlowResult(handled=False)
    return EstimateFlowResult(handled=True, reply=reply, assistant_meta=_meta_payload(draft, 0), estimate_id=None)


def _persist_structured_estimate(
    *,
    conversation_id: str,
    draft: dict[str, Any],
) -> tuple[str | None, str | None]:
    blob = draft.get("product_details") or ""
    pricing_rows = retrieve_pricing(str(blob), limit=5)
    if not pricing_rows:
        return (_PRICE_UNAVAILABLE, None)

    row = pricing_rows[0]
    if _extract_size_tuple(str(blob).lower()) is None:
        return (_PRICE_UNAVAILABLE, None)
    qty_hint = draft.get("_quantity_hint")
    qty = int(qty_hint) if isinstance(qty_hint, int) else _extract_quantity(str(blob).lower())
    if not qty:
        return (_PRICE_UNAVAILABLE, None)
    ck = _qty_to_cost_key(qty)
    raw_cost = row.get(ck) if ck else None
    if raw_cost is None:
        return (_PRICE_UNAVAILABLE, None)
    try:
        total_val = Decimal(str(raw_cost))
    except (InvalidOperation, TypeError, ValueError):
        return (_PRICE_UNAVAILABLE, None)

    if total_val <= 0:
        return (_PRICE_UNAVAILABLE, None)

    stored_total = total_val.quantize(Decimal("0.01"))
    unit_price = float(stored_total / int(qty))
    sku = (str(row.get("sku") or "").strip()) or "CATALOG_ROW"
    w, h = float(row.get("width_in") or 0), float(row.get("height_in") or 0)
    mat = str(row.get("material") or "").strip()
    fin = str(row.get("finish") or "").strip()
    short_mat = re.sub(r"^\d+(\.\d+)?\s*mil\s*", "", mat.split("/")[0]).strip()
    material_line = ", ".join(part for part in (short_mat, fin.strip().lower()) if part)
    size_label = f"{w:g} × {h:g} in" if w and h else ""
    color_m = _STEP1_COLORS_RE.search(str(blob))
    color = color_m.group(0).strip() if color_m else ""
    desc = " · ".join(part for part in (size_label, material_line, color) if part)[:650]

    items = [
        {
            "sku": sku[:240],
            "description": desc,
            "quantity": float(qty),
            "unit_price": unit_price,
            "total": float(stored_total),
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
    link = quote_page_url(eid)
    total_str = f"${float(stored_total):,.2f}"
    unit_str = "$" + re.sub(r"(\.\d\d\d*?)0+$", r"\1", f"{unit_price:.5f}")
    qty_str = f"{int(qty):,}"
    lines = ["Your quote is ready."]
    if size_label:
        lines.append(f"Size {size_label}")
    lines.append(f"Quantity {qty_str}")
    if material_line:
        lines.append(f"Material {material_line}")
    lines.extend([f"Price {unit_str} each", f"Total {total_str}", link, f"Ref: {sku}", "Reply here with any questions."])
    return "\n".join(lines), eid


@dataclass(frozen=True)
class EstimateFlowResult:
    handled: bool
    reply: str = ""
    assistant_meta: dict[str, Any] | None = None
    estimate_id: str | None = None


def sync_wizard_session_from_result(*, conversation_id: str, result: EstimateFlowResult) -> None:
    """Persist wizard draft + step to ``fortis_estimate_sessions`` after a handled turn."""
    if not result.handled:
        return
    meta = result.assistant_meta or {}
    block = meta.get("estimate_flow")
    if not isinstance(block, dict):
        return
    draft = block.get("draft")
    if not isinstance(draft, dict):
        draft = {}
    draft = dict(draft)
    active = block.get("active")
    active = True if active is None else bool(active)
    completed = block.get("completed") is True
    if completed:
        if not draft.get("product_details") and not draft.get("business_name"):
            row = fetch_estimate_session(conversation_id)
            if row and isinstance(row.get("collected_data"), dict):
                draft = _stored_collected_to_draft(row["collected_data"])
        st: EstimateSessionStatus = "completed"
    elif not active:
        st = "abandoned"
    else:
        st = "in_progress"
    fp = _first_pending_step_index(draft)
    current_step = min(fp + 1, 5)
    if completed:
        current_step = 5
    collected = _draft_to_stored_collected(draft)
    upsert_estimate_session(
        conversation_id,
        current_step=current_step,
        collected_data=collected,
        status=st,
    )


def maybe_pause_wizard_session_on_topic_shift(conversation_id: str, user_message: str) -> None:
    """If the shopper veers off (capability / SBU / frustration) mid-wizard, pause without losing data."""
    row = fetch_estimate_session(conversation_id)
    if not row or row.get("status") != "in_progress":
        return
    if should_exit_estimate_wizard_for_topic_shift(user_message):
        update_estimate_session_status(conversation_id, "paused")


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

    session_row = fetch_estimate_session(conversation_id)
    if session_row and session_row.get("status") == "in_progress":
        if should_exit_estimate_wizard_for_topic_shift(user_message):
            update_estimate_session_status(conversation_id, "paused")
            return EstimateFlowResult(handled=False)
    if session_row and session_row.get("status") == "paused":
        if should_exit_estimate_wizard_for_topic_shift(user_message):
            return EstimateFlowResult(handled=False)
        if not should_resume_estimate_wizard_from_paused(user_message):
            return EstimateFlowResult(handled=False)
        update_estimate_session_status(conversation_id, "in_progress")
        session_row = fetch_estimate_session(conversation_id)

    snap = _resolve_wizard_snap(conversation_id, conversation_history, session_row)
    carrying = snap is not None
    if not carrying and re.search(r"\bcustom\s+quote\b", raw, re.IGNORECASE):
        return EstimateFlowResult(handled=True, reply=_CUSTOM_QUOTE_REPLY)

    # Hard guard: never open the wizard on capability / refusal / SBU / general-topic openers.
    if not carrying and should_skip_estimate_wizard_opener(raw):
        return EstimateFlowResult(handled=False)

    req = is_estimate_request(raw)

    if not req and not carrying:
        return EstimateFlowResult(handled=False)

    msg_lower = raw.lower()

    if carrying and should_exit_estimate_wizard_for_topic_shift(user_message):
        row = fetch_estimate_session(conversation_id)
        if row and row.get("status") == "in_progress":
            update_estimate_session_status(conversation_id, "paused")
        return EstimateFlowResult(handled=False)

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
                    return _step1_miss_result(dict(labeled_draft), miss_txt, conversation_history, conversation_id)
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
            return _step1_miss_result(draft, miss_txt, conversation_history, conversation_id)

        if not rich:
            if should_skip_estimate_wizard_opener(raw):
                return EstimateFlowResult(handled=False)
            if _has_step1_product_signals(msg_lower):
                draft["product_details"] = raw
                qh = _extract_quantity(msg_lower)
                if qh:
                    draft["_quantity_hint"] = qh
                miss_txt = ", ".join(_step1_missing_parts(msg_lower))
                return _step1_miss_result(draft, miss_txt, conversation_history, conversation_id)
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
    new_size = _extract_size_tuple(answer.lower())
    old_size = _extract_size_tuple(str(draft.get("product_details") or "").lower())
    if new_size and old_size and (abs(new_size[0] - old_size[0]) > 0.02 or abs(new_size[1] - old_size[1]) > 0.02):
        for key in ("sku", "price", "unit_price", "total", "_quantity_hint"):
            draft.pop(key, None)
        swapped = re.sub(
            r"(\d+(?:\.\d+)?)\s*(?:x|×|\bby\b)\s*(\d+(?:\.\d+)?)",
            f"{new_size[0]:g}x{new_size[1]:g}",
            str(draft["product_details"]),
            count=1,
            flags=re.IGNORECASE,
        )
        draft["product_details"] = _merge_step1_blob(swapped, answer)
        low = str(draft["product_details"]).lower()
        fp = _first_pending_step_index(draft)
        if fp == 0:
            return _step1_miss_result(draft, ", ".join(_step1_missing_parts(low)), conversation_history, conversation_id)
        return EstimateFlowResult(
            handled=True, reply=_prompt_for_pending_index(fp), assistant_meta=_meta_payload(draft, fp), estimate_id=None
        )

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
                return _step1_miss_result(draft, miss_txt, conversation_history, conversation_id)
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
                return _step1_miss_result(draft, miss_txt, conversation_history, conversation_id)
    elif step_key == "business_name":
        issue = _business_name_field_issue(answer)
        if issue:
            sn = _safe_ack_snippet(answer)
            return EstimateFlowResult(
                handled=True,
                reply=_invalid_business_name_reply(issue, sn),
                assistant_meta=_meta_payload(draft, pending_idx),
            )
        draft["business_name"] = answer
    elif step_key == "contact_name":
        issue = _contact_name_field_issue(answer)
        if issue:
            sn = _safe_ack_snippet(answer)
            return EstimateFlowResult(
                handled=True,
                reply=_invalid_contact_name_reply(issue, sn),
                assistant_meta=_meta_payload(draft, pending_idx),
            )
        draft["contact_name"] = answer
    elif step_key == "email":
        if not _looks_like_email(answer):
            return EstimateFlowResult(
                handled=True,
                reply=_invalid_email_reply(_safe_ack_snippet(answer)),
                assistant_meta=_meta_payload(draft, pending_idx),
            )
        draft["email"] = answer
    elif step_key == "address":
        if msg_lower == "skip":
            draft["address"] = ""
        else:
            a_issue = _address_field_issue(answer)
            if a_issue:
                return EstimateFlowResult(
                    handled=True,
                    reply=_invalid_address_reply(_safe_ack_snippet(answer)),
                    assistant_meta=_meta_payload(draft, pending_idx),
                )
            draft["address"] = answer
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
