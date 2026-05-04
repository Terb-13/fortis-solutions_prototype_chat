import logging
import os
import re
from typing import Any, Dict, List

# Use the same resolved Supabase URL as api.py / store.py (SERVICE_ROLE fallback host).
from fortis_cs_agent.store import supabase

logger = logging.getLogger(__name__)


def _fortis_pricing_table() -> str:
    name = (os.getenv("FORTIS_PRICING_TABLE") or "fortis_pricing").strip()
    return name if name else "fortis_pricing"


def pricing_health_probe() -> dict[str, Any]:
    """Lightweight Supabase sanity check for operations (expose on /health)."""
    table = _fortis_pricing_table()
    out: dict[str, Any] = {"table": table, "supabase": supabase is not None}
    if not supabase:
        out["ok"] = False
        out["error"] = "supabase_disabled"
        return out
    try:
        res = supabase.table(table).select("*").limit(1).execute()
        out["ok"] = True
        out["has_rows"] = bool(res.data)
    except Exception as exc:
        out["ok"] = False
        out["error"] = type(exc).__name__
        out["error_detail"] = str(exc)[:400]
        logger.warning("pricing_health_probe failed table=%s", table, exc_info=True)
    return out


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


# Pricing table text column (Postgres / Supabase schema uses comment_application).
_FORTIS_PRICING_COMMENT_COL = "comment_application"


def _pricing_dedupe_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Stable tuple for deduping REST rows — handles missing UUID ``id`` or duplicate null ids."""
    rid = row.get("id")
    if rid is not None:
        return ("id", rid)
    sku = str(row.get("sku") or "").strip().lower()
    if sku:
        return ("sku", sku)
    return (
        "hash",
        str(row.get("width_in")),
        str(row.get("height_in")),
        str(row.get("material") or "")[:80],
        str(row.get(_FORTIS_PRICING_COMMENT_COL) or row.get("description") or "")[:120],
        str(row.get("finish") or "")[:80],
    )


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


_ROW_SIZE_TOL_IN = 0.35


def _row_matches_size_tolerance(row: dict[str, Any], tw: float, th: float, tol: float) -> bool:
    try:
        rw = float(row.get("width_in") or 0)
        rh = float(row.get("height_in") or 0)
    except (TypeError, ValueError):
        return False
    return abs(rw - tw) <= tol and abs(rh - th) <= tol


def _annotate_pricing_rows(
    rows: list[dict[str, Any]],
    query_lower: str,
    *,
    how: str,
    tol: float = _ROW_SIZE_TOL_IN,
) -> list[dict[str, Any]]:
    """Copy rows and set ``_fortis_pricing_match`` for downstream prompts."""

    sz = _extract_size_tuple(query_lower)
    tw, th = sz if sz else (None, None)

    def _match_tag(row: dict[str, Any]) -> str:
        if how == "sku":
            return "sku_exact"
        exact = tw is not None and th is not None and _row_matches_size_tolerance(row, tw, th, tol)

        if tw is None or th is None:
            if how == "material":
                return "material_match"
            if how == "keyword":
                return "keyword_match"
            if how == "fallback":
                return "closest_fallback"
            return "catalog_match"

        if exact:
            return "exact_size"
        return "closest_fallback" if how == "fallback" else "closest_size"

    return [{**dict(r), "_fortis_pricing_match": _match_tag(r)} for r in rows]


def _execute_pricing_keyword_or(term: str, limit: int) -> Any:
    """Run keyword OR against fortis_pricing with broadening filters if columns differ by env."""
    c = _FORTIS_PRICING_COMMENT_COL
    tbl = _fortis_pricing_table()
    clauses = (
        f"{c}.ilike.%{term}%,material.ilike.%{term}%,finish.ilike.%{term}%,notes.ilike.%{term}%",
        f"{c}.ilike.%{term}%,material.ilike.%{term}%,finish.ilike.%{term}%",
        f"{c}.ilike.%{term}%,material.ilike.%{term}%",
        f"{c}.ilike.%{term}%",
        f"material.ilike.%{term}%",
    )
    if not supabase:
        return None
    for filt in clauses:
        try:
            return supabase.table(tbl).select("*").or_(filt).limit(limit).execute()
        except Exception:
            logger.debug("pricing keyword clause failed tbl=%s filter=%r", tbl, filt[:120], exc_info=True)
            continue
    return None


def _closest_pricing_fallback(query_lower: str, limit: int) -> list[dict[str, Any]]:
    """When targeted paths yield nothing — load a slab of the pricing table and rank."""
    if not supabase:
        return []

    tbl = _fortis_pricing_table()
    mentioned_materials = sorted({m for m in _MATERIAL_HINT_TERMS if m in query_lower})
    size_tuple = _extract_size_tuple(query_lower)
    pool: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    def _add(rows: list[dict[str, Any]]) -> None:
        for row in rows:
            r = dict(row)
            key = _pricing_dedupe_key(r)
            if key in seen:
                continue
            seen.add(key)
            pool.append(r)

    try:
        base = (
            supabase.table(tbl).select("*").limit(max(400, limit * 80)).execute()
        )
        _add(list(base.data or []))
        for material in mentioned_materials[:3]:
            try:
                res_mat = (
                    supabase.table(tbl)
                    .select("*")
                    .ilike("material", f"%{material}%")
                    .limit(120)
                    .execute()
                )
                _add(list(res_mat.data or []))
            except Exception:
                logger.debug("fallback material subset failed material=%r", material, exc_info=True)
    except Exception:
        logger.warning("_closest_pricing_fallback fetch failed tbl=%s", tbl, exc_info=True)
        return []

    if not pool:
        logger.warning(
            "retrieve_pricing: table %s returned zero rows (empty table, wrong table name, or deny policy).",
            tbl,
        )
        return []

    if size_tuple:
        tw, th = size_tuple

        def _distance(row: dict[str, Any]) -> float:
            try:
                rw = float(row.get("width_in") or 0)
                rh = float(row.get("height_in") or 0)
            except (TypeError, ValueError):
                return 9999.0
            return abs(rw - tw) + abs(rh - th)

        ranked = _pick_nearest_size_diverse_materials(
            pool,
            width_val=tw,
            height_val=th,
            limit=max(limit * 4, limit),
        )
        if mentioned_materials:
            ranked = sorted(
                ranked,
                key=lambda r: (
                    -_material_match_score(query_lower, str(r.get("material") or "")),
                    _distance(r),
                ),
            )
        return ranked[:limit]

    ranked = sorted(
        pool,
        key=lambda r: -_material_match_score(query_lower, str(r.get("material") or "")),
    )
    return ranked[:limit]


def retrieve_pricing(query: str, limit: int = 5) -> list[dict]:
    """
    Smart pricing retrieval from ``fortis_pricing``: SKU → material → size (+ nearest die) →
    keywords → closest-match fallback over the catalog pool.

    Each row may include ``_fortis_pricing_match``::

        sku_exact | exact_size | closest_size | material_match | keyword_match |
        closest_fallback | catalog_match
    """
    if not supabase or not query:
        return []

    query_lower = query.lower()
    tbl = _fortis_pricing_table()
    tol = _ROW_SIZE_TOL_IN
    mentioned_materials = sorted({m for m in _MATERIAL_HINT_TERMS if m in query_lower})

    # --- 1) SKU ---
    sku_match = re.search(r"(sticker_[a-z0-9.]+)", query_lower)
    if sku_match:
        sku = sku_match.group(1).upper()
        try:
            res = supabase.table(tbl).select("*").ilike("sku", f"%{sku}%").limit(1).execute()
            if res.data:
                return _annotate_pricing_rows(list(res.data)[:limit], query_lower, how="sku", tol=tol)
        except Exception:
            logger.warning("retrieve_pricing SKU lookup failed sku=%r", sku, exc_info=True)

    # --- 2) Material ---
    results: list[dict[str, Any]] = []
    seen_mat: set[tuple[Any, ...]] = set()

    def _take_material_row(row_raw: dict[str, Any]) -> bool:
        row = dict(row_raw)
        dk = _pricing_dedupe_key(row)
        if dk in seen_mat:
            return len(results) >= limit
        seen_mat.add(dk)
        results.append(row)
        return len(results) >= limit

    for material in mentioned_materials[:3]:
        try:
            res = (
                supabase.table(tbl)
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
            if _take_material_row(row):
                return _annotate_pricing_rows(results[:limit], query_lower, how="material", tol=tol)

    if results:
        return _annotate_pricing_rows(results[:limit], query_lower, how="material", tol=tol)

    # --- 3) Size bucket + nearest-match (WxH × by) ---
    size_tuple_pre = _extract_size_tuple(query_lower)
    if size_tuple_pre:
        width_val, height_val = size_tuple_pre
        try:
            tight = (
                supabase.table(tbl)
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
                broad = supabase.table(tbl).select("*").limit(150).execute()
                candidates = list(broad.data or [])
        except Exception:
            logger.warning("retrieve_pricing size-bucket query failed", exc_info=True)
            candidates = []
        if not candidates:
            try:
                broad = supabase.table(tbl).select("*").limit(150).execute()
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
        if mentioned_materials:
            size_ranked = sorted(
                size_ranked,
                key=lambda r: (
                    -_material_match_score(query_lower, str(r.get("material") or "")),
                    _distance(r),
                ),
            )
        sliced = [dict(r) for r in size_ranked[:limit]]
        if sliced:
            return _annotate_pricing_rows(sliced, query_lower, how="size", tol=tol)

    # --- 4) Keywords OR ---
    results = []
    seen_kw: set[tuple[Any, ...]] = set()
    keywords = [k for k in query_lower.split() if len(k) > 2]
    for keyword in keywords[:6]:
        term = _safe_ilike_term(keyword)
        if not term:
            continue
        res_kw = _execute_pricing_keyword_or(term, limit)
        if res_kw is None:
            logger.warning(
                "retrieve_pricing keyword OR exhausted all clauses term=%r table=%s", term, tbl
            )
            continue
        for row in res_kw.data or []:
            r = dict(row)
            dk = _pricing_dedupe_key(r)
            if dk in seen_kw:
                continue
            seen_kw.add(dk)
            results.append(r)
            if len(results) >= limit:
                return _annotate_pricing_rows(results[:limit], query_lower, how="keyword", tol=tol)

    if results:
        return _annotate_pricing_rows(results[:limit], query_lower, how="keyword", tol=tol)

    # --- 5) Whole-catalog closest match ---
    fb = _closest_pricing_fallback(query_lower, limit)
    if fb:
        return _annotate_pricing_rows(fb, query_lower, how="fallback", tol=tol)

    logger.warning(
        "retrieve_pricing returned no rows (table=%r query_prefix=%r). Check GET /health field pricing_health.",
        tbl,
        query.strip()[:120],
    )
    return []


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
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:x|×|\bby\b)\s*(\d+(?:\.\d+)?)",
        query_lower,
        re.IGNORECASE,
    )
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


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


def _mentioned_qty_tiers(query_lower: str) -> list[int]:
    """Detect standard order quantities the user wants compared (500 / 1000 / 2500)."""
    standard = (100, 250, 500, 1000, 1500, 2000, 2500, 3000, 4000, 5000)
    found: list[int] = []
    for n in standard:
        if re.search(rf"\b{n}\b", query_lower):
            found.append(n)
    return found


def _exact_row_for_size(
    rows: list[dict[str, Any]],
    tw: float,
    th: float,
    *,
    tol: float = _ROW_SIZE_TOL_IN,
) -> bool:
    return any(_row_matches_size_tolerance(r, tw, th, tol) for r in rows)


def format_pricing_context(rows: list[dict[str, Any]], query: str) -> str:
    """
    Build compact pricing context with guided next-step hints for the model.
    """
    if not rows:
        return ""

    query_lower = query.lower()
    qty = _extract_quantity_from_query(query_lower)

    qty_key = _qty_to_cost_key(qty) if qty is not None else "cost_1000"

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

    tier_qtys = _mentioned_qty_tiers(query_lower)

    match_hints = [str(row.get("_fortis_pricing_match") or "") for row in rows[:5]]
    is_closest_catalog = any(h in ("closest_size", "closest_fallback") for h in match_hints)

    lines: list[str] = []
    for row in rows[:5]:
        sku = row.get("sku") or "Unknown SKU"
        description = (row.get("comment_application") or row.get("description") or "").strip() or "No description"
        material = row.get("material") or "N/A"
        finish = row.get("finish") or "N/A"
        selected_cost = row.get(qty_key)
        cost_label = qty_key.replace("cost_", "").upper()
        tag = row.get("_fortis_pricing_match")
        tag_note = (
            f" | (nearest catalog match: {tag})"
            if tag in ("closest_size", "closest_fallback")
            else ""
        )
        lines.append(
            f"- {sku} | {description} | Material: {material} | Finish: {finish} | "
            f"Cost@{cost_label}: {selected_cost}{tag_note}"
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

    header = (
        "Pricing Agent Context (Quick Ship labels / fortis_pricing ONLY — not pouches, shipper boxes, or unrelated packaging):"
    )
    if is_closest_catalog:
        header += (
            "\nNearest-match note: No exact SKU/product match was found for the request as stated. Recommend the closest "
            "rows below explicitly; confirm material, finish, and dimensions with the customer before locking a quote."
        )

    parts = [header, "\n".join(lines)]

    if size_tuple:
        tw, th = size_tuple
        if _exact_row_for_size(rows, tw, th):
            parts.append(f"Catalog truth: An exact {tw}×{th} in. row exists in the snapshot below.")
        else:
            closest = rows[0]
            cw = closest.get("width_in")
            ch = closest.get("height_in")
            cmat = closest.get("material") or "N/A"
            cfinish = closest.get("finish") or "N/A"
            parts.append(
                f"Catalog truth: No exact {tw}×{th} in. row in this pricing snapshot — closest row shown is "
                f"{cw}×{ch} in. Say that clearly; do not imply an exact {tw}×{th} die exists unless it appears above."
            )
            parts.append(
                f"Closest-size recommendation: Direct the customer to {cw}×{ch} in. as the nearest available option "
                f"(material: {cmat}; finish: {cfinish}). Ask if they want to proceed with this nearest size or request custom tooling."
            )

    if rows and any(
        "bopp" in str(r.get("material") or "").lower()
        for r in rows[:5]
    ):
        parts.append(
            "BOPP check: At least one listed row uses BOPP-family material in the material column — cite those rows only."
        )

    if len(rows) == 1:
        parts.append(
            "Limited catalog: Only one pricing row is loaded — offer qualitative alternatives (paper vs vinyl) only as "
            "general guidance, not as priced SKUs, unless additional rows appear above."
        )

    if tier_qtys and len(tier_qtys) >= 2 and rows:
        prime = rows[0]
        tier_bits = []
        for n in tier_qtys[:5]:
            ck = _qty_to_cost_key(n)
            tier_bits.append(f"{n} qty → {ck}={prime.get(ck)}")
        parts.append("Customer requested multiple qty breakpoints (use these indicative totals): " + "; ".join(tier_bits))

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

    wants_more_opts = any(
        phrase in query_lower
        for phrase in (
            "2-3",
            "2–3",
            "two or three",
            "few options",
            "close options",
            "compare",
            "alternatives",
        )
    )

    if asks_finish_options and distinct_finishes:
        parts.append(
            "Finish step: User asked what finish options exist — give a one-line summary (e.g. gloss vs matte) "
            "using the finishes listed above only; then ask which they prefer."
        )
    elif needs_material_prompt and not wants_more_opts:
        parts.append(
            "Material step: User did not specify material/finish. Ask once which material they prefer "
            "(e.g. white BOPP vs paper vs vinyl). Offer up to 2–3 options only from rows listed above — "
            "never substitute pouch/film flexible-packaging examples."
        )
    elif wants_more_opts:
        parts.append(
            "Options step: User asked for more options — reply ONLY with separate rows from the list above "
            "(different material/finish/SKU lines). If only one row exists, say so honestly and describe trade-offs briefly "
            "without inventing SKUs or pouch pricing."
        )
    else:
        parts.append(
            "Material step: User hinted at material — briefly confirm and pick the closest catalog row; "
            "keep BOPP explanation to one short clause."
        )

    parts.append(
        "Quote completion: When required customer fields + SKU detail are settled, assistants emit STRICT JSON payloads "
        "for the backend (see system prompt). Map totals/unit_price ONLY from Cost@ tiers listed above—never hallucinate SKU "
        "dollar amounts in conversational prose beforehand."
    )
    parts.append(
        "PDF alternate only: Reserve generate_estimate_pdf for explicit downloadable PDF tooling outside structured JSON quotes."
    )
    parts.append(
        "Guided follow-up: One question only when possible; labels only — never pivot to pouches/corrugated unless the user asks."
    )

    return "\n".join(parts)
