import logging
import os
import re
from typing import Any, Dict, List

from supabase import create_client

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_KEY else None


def _safe_ilike_term(raw: str) -> str | None:
    """Strip characters that break PostgREST `or` / `ilike` filters (commas, quotes, etc.)."""
    t = re.sub(r"[^a-z0-9.\-x]+", "", (raw or "").lower())
    if len(t) < 3:
        return None
    return t[:48]


def retrieve_knowledge(query: str, limit: int = 5) -> List[Dict]:
    """
    Search fortis_knowledge table for relevant content.
    Uses simple ILIKE search for now (fast). Can upgrade to vector search later.
    """
    if not supabase or not query:
        return []

    keywords = [k.strip() for k in query.lower().split() if len(k) > 2]

    results = []
    seen_ids = set()

    for keyword in keywords[:6]:
        term = _safe_ilike_term(keyword)
        if not term:
            continue
        try:
            res = (
                supabase.table("fortis_knowledge")
                .select("id, title, content, category, source")
                .ilike("content", f"%{term}%")
                .limit(limit)
                .execute()
            )
        except Exception:
            logger.warning("retrieve_knowledge Supabase query failed term=%r", term, exc_info=True)
            continue
        for row in res.data or []:
            if row["id"] not in seen_ids:
                seen_ids.add(row["id"])
                results.append(row)
                if len(results) >= limit:
                    return results

    return results


_MATERIAL_HINT_TERMS = frozenset(
    [
        "bopp",
        "wbgp",
        "paper",
        "vinyl",
        "poly",
        "pet",
        "pp",
        "pe",
        "film",
        "foil",
        "thermal",
        "synthetic",
        "clear",
        "white",
        "matte",
        "gloss",
        "uncoated",
        "coated",
        "removable",
        "permanent",
        "destructible",
        "void",
    ]
)


def _user_mentioned_material(query_lower: str) -> bool:
    if any(term in query_lower for term in _MATERIAL_HINT_TERMS):
        return True
    if re.search(r"\b\d+(?:\.\d+)?\s*mil\b", query_lower):
        return True
    return False


def _pick_nearest_size_diverse_materials(
    candidates: list[dict[str, Any]],
    *,
    width_val: float,
    height_val: float,
    limit: int,
) -> list[dict[str, Any]]:
    """Prefer smallest size delta; include multiple rows so different materials appear."""

    def _dist(row: dict[str, Any]) -> float:
        try:
            rw = float(row.get("width_in") or 0)
            rh = float(row.get("height_in") or 0)
        except (TypeError, ValueError):
            return 9999.0
        return abs(rw - width_val) + abs(rh - height_val)

    sorted_all = sorted(candidates, key=_dist)
    picked: list[dict[str, Any]] = []
    seen_material: set[str] = set()

    for row in sorted_all:
        mat_key = (row.get("material") or "").strip().lower()[:120] or "__unknown__"
        if mat_key not in seen_material:
            seen_material.add(mat_key)
            picked.append(row)
            if len(picked) >= limit:
                return picked

    for row in sorted_all:
        if row not in picked:
            picked.append(row)
        if len(picked) >= limit:
            break
    return picked


def _material_match_score(query_lower: str, material: str) -> float:
    """Rough overlap score for ranking catalog materials against user text."""
    if not material:
        return 0.0
    mat_lower = material.lower()
    score = 0.0
    for term in _MATERIAL_HINT_TERMS:
        if term in query_lower and term in mat_lower:
            score += 2.0
        elif term in query_lower:
            pass
    for token in re.findall(r"[a-z]{3,}", query_lower):
        if len(token) > 12:
            continue
        if token in mat_lower:
            score += 0.5
    return score


def retrieve_pricing(query: str, limit: int = 5) -> list[dict]:
    """
    Smart pricing retrieval from fortis_pricing table.
    Supports SKU lookup, material filtering, and quantity-based pricing.
    """
    if not supabase or not query:
        return []

    query_lower = query.lower()
    results: list[dict[str, Any]] = []

    # 1. Try exact SKU match first
    sku_match = re.search(r"(sticker_[a-z0-9.]+)", query_lower)
    if sku_match:
        sku = sku_match.group(1).upper()
        try:
            res = supabase.table("fortis_pricing").select("*").ilike("sku", f"%{sku}%").limit(1).execute()
            if res.data:
                return res.data
        except Exception:
            logger.warning("retrieve_pricing SKU lookup failed sku=%r", sku, exc_info=True)

    # 2. Material-aware query path (substring + ILIKE on catalog material)
    mentioned_materials = sorted({m for m in _MATERIAL_HINT_TERMS if m in query_lower})
    for material in mentioned_materials[:3]:
        try:
            res = (
                supabase.table("fortis_pricing")
                .select("*")
                .ilike("material", f"%{material}%")
                .limit(max(limit * 2, 8))
                .execute()
            )
        except Exception:
            logger.warning("retrieve_pricing material filter failed material=%r", material, exc_info=True)
            continue
        scored = sorted(
            res.data or [],
            key=lambda r: (-_material_match_score(query_lower, str(r.get("material") or "")), str(r.get("sku"))),
        )
        for row in scored:
            if row not in results:
                results.append(row)
                if len(results) >= limit:
                    return results

    # 3. Size-aware query path (e.g. "3x8"), with nearest-match fallback.
    size_match = re.search(r"(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)", query_lower)
    if size_match:
        width_val = float(size_match.group(1))
        height_val = float(size_match.group(2))
        tol = 0.35  # tight tolerance in inches
        try:
            tight = (
                supabase.table("fortis_pricing")
                .select("*")
                .gte("width_in", width_val - tol)
                .lte("width_in", width_val + tol)
                .gte("height_in", height_val - tol)
                .lte("height_in", height_val + tol)
                .limit(max(limit * 4, 12))
                .execute()
            )
            candidates = list(tight.data or [])
            if not candidates:
                broad = supabase.table("fortis_pricing").select("*").limit(150).execute()
                candidates = list(broad.data or [])
        except Exception:
            logger.warning("retrieve_pricing size-bucket query failed", exc_info=True)
            candidates = []
        if not candidates:
            try:
                broad = supabase.table("fortis_pricing").select("*").limit(150).execute()
                candidates = list(broad.data or [])
            except Exception:
                logger.warning("retrieve_pricing broad fetch failed", exc_info=True)
                candidates = []

        def _distance(row: dict[str, Any]) -> float:
            try:
                rw = float(row.get("width_in") or 0)
                rh = float(row.get("height_in") or 0)
            except (TypeError, ValueError):
                return 9999.0
            return abs(rw - width_val) + abs(rh - height_val)

        size_ranked = _pick_nearest_size_diverse_materials(
            candidates,
            width_val=width_val,
            height_val=height_val,
            limit=limit,
        )
        # If user hinted material, prefer rows whose material text aligns (still size-ranked pool).
        if mentioned_materials:
            size_ranked = sorted(
                size_ranked,
                key=lambda r: (
                    -_material_match_score(query_lower, str(r.get("material") or "")),
                    _distance(r),
                ),
            )
        for row in size_ranked:
            if row not in results:
                results.append(row)
                if len(results) >= limit:
                    return results

    # 4. Keyword search across material, description, notes
    keywords = [k for k in query_lower.split() if len(k) > 2]
    for keyword in keywords[:6]:
        term = _safe_ilike_term(keyword)
        if not term:
            continue
        try:
            res = (
                supabase.table("fortis_pricing")
                .select("*")
                .or_(f"description.ilike.%{term}%,material.ilike.%{term}%,notes.ilike.%{term}%")
                .limit(limit)
                .execute()
            )
        except Exception:
            logger.warning("retrieve_pricing keyword OR failed term=%r", term, exc_info=True)
            continue
        for row in res.data or []:
            if row not in results:
                results.append(row)
                if len(results) >= limit:
                    return results

    return results


def _extract_quantity_from_query(query_lower: str) -> int | None:
    for pat in (
        r"\bquantity\s*(?:is|are|=|:)?\s*(\d{1,6})\b",
        r"\bqty\s*(?:is|are|=|:)?\s*(\d{1,6})\b",
        r"\b(\d{1,6})\s*(?:labels|units)\b",
    ):
        m = re.search(pat, query_lower)
        if m:
            q = int(m.group(1))
            if 1 <= q <= 999_999:
                return q
    m = re.search(r"\b(\d{2,6})\b", query_lower)
    if m:
        q = int(m.group(1))
        if 10 <= q <= 999_999:
            return q
    return None


def _extract_size_tuple(query_lower: str) -> tuple[float, float] | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)", query_lower)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


def format_pricing_context(rows: list[dict[str, Any]], query: str) -> str:
    """
    Build compact pricing context with guided next-step hints for the model.
    """
    if not rows:
        return ""

    query_lower = query.lower()
    qty = _extract_quantity_from_query(query_lower)

    qty_key = "cost_1000"
    if qty is not None:
        if qty >= 5000:
            qty_key = "cost_5000"
        elif qty >= 4000:
            qty_key = "cost_4000"
        elif qty >= 3000:
            qty_key = "cost_3000"
        elif qty >= 2500:
            qty_key = "cost_2500"
        elif qty >= 2000:
            qty_key = "cost_2000"
        elif qty >= 1500:
            qty_key = "cost_1500"
        elif qty >= 1000:
            qty_key = "cost_1000"
        elif qty >= 500:
            qty_key = "cost_500"
        elif qty >= 250:
            qty_key = "cost_250"
        else:
            qty_key = "cost_100"

    needs_material_prompt = not _user_mentioned_material(query_lower)
    size_tuple = _extract_size_tuple(query_lower)
    asks_finish_options = any(
        phrase in query_lower
        for phrase in (
            "finish",
            "what options",
            "which options",
            "options do",
            "kind of finish",
            "gloss or matte",
            "matte or gloss",
        )
    )

    lines: list[str] = []
    for row in rows[:5]:
        sku = row.get("sku") or "Unknown SKU"
        description = row.get("description") or "No description"
        material = row.get("material") or "N/A"
        finish = row.get("finish") or "N/A"
        selected_cost = row.get(qty_key)
        cost_label = qty_key.replace("cost_", "").upper()
        lines.append(
            f"- {sku} | {description} | Material: {material} | Finish: {finish} | "
            f"Cost@{cost_label}: {selected_cost}"
        )

    distinct_materials: list[str] = []
    seen_m: set[str] = set()
    for row in rows[:8]:
        m = (row.get("material") or "").strip()
        if not m:
            continue
        key = m.lower()
        if key not in seen_m:
            seen_m.add(key)
            distinct_materials.append(m)

    parts = ["Pricing Agent Context:", "\n".join(lines)]

    if distinct_materials:
        parts.append(
            "Closest catalog materials represented above (use these to suggest alternatives): "
            + "; ".join(distinct_materials[:4])
        )

    distinct_finishes: list[str] = []
    seen_f: set[str] = set()
    for row in rows[:12]:
        f = (row.get("finish") or "").strip()
        if not f:
            continue
        key = f.lower()
        if key not in seen_f:
            seen_f.add(key)
            distinct_finishes.append(f)

    if distinct_finishes:
        parts.append("Finishes on closest matches (answer finish questions from this list only): " + "; ".join(distinct_finishes[:6]))

    memory_hints: list[str] = []
    if size_tuple:
        memory_hints.append(f"User stated size ~{size_tuple[0]}x{size_tuple[1]} in.; do not ask for dimensions again.")
    if qty is not None:
        memory_hints.append(f"User stated quantity {qty}; do not ask quantity again unless clarifying.")
    if _user_mentioned_material(query_lower):
        memory_hints.append("User stated or chose a material direction; do not re-explain unrelated product categories.")
    if memory_hints:
        parts.append("Conversation discipline: " + " ".join(memory_hints))

    if asks_finish_options and distinct_finishes:
        parts.append(
            "Finish step: User asked what finish options exist — give a one-line summary (e.g. gloss vs matte) "
            "using the finishes listed above only; then ask which they prefer."
        )
    elif needs_material_prompt:
        parts.append(
            "Material step (required): User did not specify material/finish. Ask which material they prefer "
            "(e.g. white BOPP vs paper vs vinyl) and offer to compare 2–3 close options from the list above. "
            "Do not assume one material without confirming."
        )
    else:
        parts.append(
            "Material step: User hinted at material — briefly confirm and pick the closest catalog row; "
            "keep BOPP explanation to one short clause."
        )

    parts.append(
        "Guided follow-up: One question only when possible; stay on labels if they are quoting labels."
    )

    return "\n".join(parts)
