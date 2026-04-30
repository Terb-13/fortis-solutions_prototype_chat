import os
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
