"""Knowledge & FAQ retrieval — stop-word filter, multi-column weighted ranking.

Locks in the SBU regression: with stop-words excluded and the ranking
weighted across title/category/content, querying "tell me about the sbu"
returns the SBU-tagged row, not noise from rows that happen to contain
"tell" or "about" in their content.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock, patch


def _mock_supabase_returning(rows: list[dict[str, Any]]) -> MagicMock:
    """Build a MagicMock that mimics the supabase-py chain and returns `rows`
    on every `.execute()` call.

    The real Supabase client filters server-side via PostgREST. The mock
    skips that filtering and returns the same rows for every keyword query —
    the retrieval ranking algorithm then independently checks which keywords
    appear in which columns of each row, so tests still verify the algorithm
    correctly without re-implementing PostgREST filtering.
    """
    sb = MagicMock()
    chain = sb.table.return_value
    chain.select.return_value = chain
    chain.or_.return_value = chain
    chain.eq.return_value = chain
    chain.ilike.return_value = chain
    chain.limit.return_value = chain
    chain.order.return_value = chain
    response = MagicMock()
    response.data = rows
    chain.execute.return_value = response
    return sb


class TestRetrieveKnowledgeRanking(unittest.TestCase):
    def test_sbu_query_ranks_sbu_row_first(self) -> None:
        """Brett's regression case: 'tell me about the sbu' must surface the
        SBU-tagged row above noise rows that contain stop-words like 'tell'
        or 'about' in their body."""
        rows = [
            {
                "id": "noise-tell-1",
                "title": "Email about a label issue",
                "content": "...the customer asked us to tell them when their order ships...",
                "category": "email",
                "source": "email-history",
            },
            {
                "id": "noise-tell-2",
                "title": "Status update",
                "content": "I'll tell the team about the next batch.",
                "category": "transcript",
                "source": "transcript",
            },
            {
                "id": "sbu-row",
                "title": "SBU Update Row 3",
                "content": "Fortis Edge SBU launched as a digital-speed business unit serving Tier 3/4 customers.",
                "category": "sbu",
                "source": "sbu-status-update",
            },
            {
                "id": "unrelated-1",
                "title": "Adhesion guidance",
                "content": "When BOPP fails to adhere properly, recommend permanent acrylic.",
                "category": "guidance",
                "source": "knowledge",
            },
        ]
        sb_mock = _mock_supabase_returning(rows)
        with patch("fortis_cs_agent.knowledge.supabase", sb_mock):
            from fortis_cs_agent.knowledge import retrieve_knowledge

            results = retrieve_knowledge("tell me about the sbu", limit=5)

        # The SBU row must be in results AND ranked first.
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["id"], "sbu-row")

        # Noise rows that match no non-stop-word keyword get filtered out.
        result_ids = {r["id"] for r in results}
        self.assertNotIn("noise-tell-1", result_ids)
        self.assertNotIn("noise-tell-2", result_ids)

    def test_only_stopwords_returns_empty_without_querying(self) -> None:
        """Query of pure stop-words ('what is this') yields no keywords —
        Supabase shouldn't even be hit."""
        sb_mock = _mock_supabase_returning([])
        with patch("fortis_cs_agent.knowledge.supabase", sb_mock):
            from fortis_cs_agent.knowledge import retrieve_knowledge

            results = retrieve_knowledge("what is this", limit=5)

        self.assertEqual(results, [])
        sb_mock.table.assert_not_called()

    def test_match_via_title_only(self) -> None:
        """A row whose `title` contains the keyword but `content` does not
        should still be retrieved — multi-column search."""
        rows = [
            {
                "id": "title-match",
                "title": "Adhesion troubleshooting",
                "content": "When labels peel, check the substrate compatibility.",
                "category": "guidance",
                "source": "knowledge",
            },
            {
                "id": "noise",
                "title": "Order status",
                "content": "Standard shipping takes 5 days.",
                "category": "process",
                "source": "knowledge",
            },
        ]
        sb_mock = _mock_supabase_returning(rows)
        with patch("fortis_cs_agent.knowledge.supabase", sb_mock):
            from fortis_cs_agent.knowledge import retrieve_knowledge

            results = retrieve_knowledge("adhesion problems", limit=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "title-match")

    def test_no_supabase_returns_empty(self) -> None:
        """Preserves dev-mode silent-empty when Supabase isn't configured."""
        with patch("fortis_cs_agent.knowledge.supabase", None):
            from fortis_cs_agent.knowledge import retrieve_knowledge

            results = retrieve_knowledge("anything", limit=5)
        self.assertEqual(results, [])


class TestRetrieveFaqRanking(unittest.TestCase):
    def test_question_match_ranks_first(self) -> None:
        """An FAQ whose `question` directly matches the user's query ranks
        above an unrelated FAQ."""
        rows = [
            {
                "id": "faq-1",
                "question": "What is the SBU?",
                "answer": "Fortis Edge is the SBU serving Tier 3/4 customers.",
                "category": "general",
                "published": True,
            },
            {
                "id": "faq-2",
                "question": "How do I reorder?",
                "answer": "Use the portal reorder flow.",
                "category": "ordering",
                "published": True,
            },
        ]
        sb_mock = _mock_supabase_returning(rows)
        with patch("fortis_cs_agent.knowledge.supabase", sb_mock):
            from fortis_cs_agent.knowledge import retrieve_faq

            results = retrieve_faq("tell me about the sbu", limit=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "faq-1")

    def test_no_match_returns_empty(self) -> None:
        """An FAQ that shares no keywords with the query returns an empty list."""
        rows = [
            {
                "id": "faq-1",
                "question": "How do I reorder?",
                "answer": "Use the portal.",
                "category": "ordering",
                "published": True,
            },
        ]
        sb_mock = _mock_supabase_returning(rows)
        with patch("fortis_cs_agent.knowledge.supabase", sb_mock):
            from fortis_cs_agent.knowledge import retrieve_faq

            results = retrieve_faq("totally unrelated query xyz", limit=5)

        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
