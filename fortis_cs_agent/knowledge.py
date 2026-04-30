import os
import re
from typing import Dict, List
from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_KEY else None


def retrieve_knowledge(query: str, limit: int = 5) -> List[Dict]:
    """
    Search fortis_knowledge table for relevant content.
    Uses simple ILIKE search for now (fast). Can upgrade to vector search later.
    """
    if not supabase or not query:
        return []

    # Split query into keywords
    keywords = [k.strip() for k in query.lower().split() if len(k) > 2]

    results = []
    seen_ids = set()

    for keyword in keywords[:6]:  # limit to first 6 keywords
        res = (
            supabase.table("fortis_knowledge")
            .select("id, title, content, category, source")
            .ilike("content", f"%{keyword}%")
            .limit(limit)
            .execute()
        )
        for row in res.data:
            if row["id"] not in seen_ids:
                seen_ids.add(row["id"])
                results.append(row)
                if len(results) >= limit:
                    return results

    return results


def retrieve_pricing(query: str, limit: int = 5) -> list[dict]:
    """
    Smart pricing retrieval from fortis_pricing table.
    Supports SKU lookup, material filtering, and quantity-based pricing.
    """
    if not supabase or not query:
        return []

    query_lower = query.lower()
    results = []

    # 1. Try exact SKU match first
    sku_match = re.search(r"(sticker_[a-z0-9.]+)", query_lower)
    if sku_match:
        sku = sku_match.group(1).upper()
        res = supabase.table("fortis_pricing").select("*").ilike("sku", f"%{sku}%").limit(1).execute()
        if res.data:
            return res.data

    # 2. Keyword search across material, description, notes
    keywords = [k for k in query_lower.split() if len(k) > 2]
    for keyword in keywords[:4]:
        res = (
            supabase.table("fortis_pricing")
            .select("*")
            .or_(f"description.ilike.%{keyword}%,material.ilike.%{keyword}%,notes.ilike.%{keyword}%")
            .limit(limit)
            .execute()
        )
        for row in res.data:
            if row not in results:
                results.append(row)
                if len(results) >= limit:
                    return results

    return results
