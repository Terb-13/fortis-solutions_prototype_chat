"""
Detect and validate estimate JSON authored by the Grok assistant (structured quotes).

Parsing is forgiving (raw JSON vs ```json … ``` fenced blocks vs first balanced object).
"""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

_ESTIMATE_TOP_KEYS = frozenset({"business_name", "contact_name", "email", "items"})


def _strip_json_fence(text: str) -> str | None:
    """Return inner payload if fenced with ```json … ```."""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if not m:
        return None
    inner = m.group(1).strip()
    if inner.startswith("{"):
        return inner
    extracted = _balanced_object_from(inner)
    return extracted


def _balanced_object_from(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    quote_chr = ""

    i = start
    while i < len(text):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote_chr:
                in_str = False
        else:
            if ch in "\"'":
                in_str = True
                quote_chr = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        i += 1
    return None


def parse_assistant_estimate_json(raw: str) -> dict[str, Any] | None:
    """Return validated estimate dict, or ``None`` if ``raw`` does not encode a formal estimate."""
    text = (raw or "").strip()
    if not text:
        return None

    fenced = _strip_json_fence(text) or ""

    candidates: list[str] = []
    for part in (
        fenced,
        text,
        text[text.find("{") :] if "{" in text else "",
        _balanced_object_from(text) or "",
    ):
        piece = (part or "").strip()
        if piece and piece not in candidates:
            candidates.append(piece)

    for cand in candidates:
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and _ESTIMATE_TOP_KEYS <= obj.keys():
            if _shape_ok(obj):
                return _normalize_estimate_dict(obj)

    return None


def _coerce_money(v: Any) -> Decimal | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    if isinstance(v, str) and v.strip():
        try:
            return Decimal(v.strip().replace(",", ""))
        except InvalidOperation:
            return None
    return None


def _shape_ok(obj: dict[str, Any]) -> bool:
    business = str(obj.get("business_name") or "").strip()
    contact = str(obj.get("contact_name") or "").strip()
    email = str(obj.get("email") or "").strip()
    items = obj.get("items")

    if not business or not contact or not email:
        return False
    if not isinstance(items, list) or len(items) < 1:
        return False

    for idx, row in enumerate(items):
        if not isinstance(row, dict):
            logger.debug("estimate_json invalid items[%s] not-object", idx)
            return False
        sku = str(row.get("sku") or "").strip()
        desc = str(row.get("description") or "").strip()
        qty = row.get("quantity")
        unit = _coerce_money(row.get("unit_price"))
        tot = _coerce_money(row.get("total"))

        qty_ok = isinstance(qty, (int, float)) and float(qty) > 0
        if isinstance(qty, str) and qty.strip():
            try:
                qf = float(qty.strip().replace(",", ""))
                qty_ok = qf > 0
            except ValueError:
                qty_ok = False

        if not sku or not desc or not qty_ok or unit is None or tot is None:
            logger.debug(
                "estimate_json invalid items[%s] sku=%s desc=%s qty=%s unit=%s total=%s",
                idx,
                bool(sku),
                bool(desc),
                qty_ok,
                unit,
                tot,
            )
            return False
        if unit < 0 or tot < 0:
            return False

    return True


def _normalize_estimate_dict(obj: dict[str, Any]) -> dict[str, Any]:
    """Return copy safe to pass into ``insert_fortis_estimate`` (plain serializable primitives)."""
    items_out: list[dict[str, Any]] = []
    for row_raw in obj.get("items") or []:
        if not isinstance(row_raw, dict):
            continue
        qty_raw = row_raw.get("quantity")
        if isinstance(qty_raw, str):
            quantity = float(qty_raw.strip().replace(",", ""))
        else:
            quantity = float(qty_raw)

        unit = _coerce_money(row_raw.get("unit_price"))
        tot = _coerce_money(row_raw.get("total"))
        assert unit is not None and tot is not None

        items_out.append(
            {
                "sku": str(row_raw.get("sku") or "").strip(),
                "description": str(row_raw.get("description") or "").strip(),
                "quantity": quantity,
                "unit_price": float(unit),
                "total": float(tot),
            }
        )

    notes = obj.get("notes")
    notes_str = (
        str(notes).strip()
        if notes is not None
        else "This quote does not include shipping or taxes. Prices are valid for 30 days."
    )

    return {
        "business_name": str(obj.get("business_name") or "").strip(),
        "contact_name": str(obj.get("contact_name") or "").strip(),
        "email": str(obj.get("email") or "").strip(),
        "phone": str(obj.get("phone") or "").strip(),
        "address": str(obj.get("address") or "").strip(),
        "items": items_out,
        "notes": notes_str,
    }


def compact_history_hint() -> str:
    """Displayed to the model instead of huge raw estimate JSON assistant turns."""
    return "[Estimate JSON was captured and saved earlier in this conversation — continue naturally; never reset the thread.]"


def looks_like_estimate_json_assistant_turn(content: str) -> bool:
    return parse_assistant_estimate_json(content) is not None
