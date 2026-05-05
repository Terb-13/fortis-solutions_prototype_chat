"""Guards around knowledge RAG for generic questions (avoid transcript-as-customer confusion)."""

from __future__ import annotations

import unittest

from fortis_cs_agent.api import _should_skip_knowledge_retrieval


class TestKnowledgeSkip(unittest.TestCase):
    def test_capability_questions_skip_rag(self) -> None:
        self.assertTrue(_should_skip_knowledge_retrieval("what can you do?"))
        self.assertTrue(_should_skip_knowledge_retrieval("How can you help"))
        self.assertTrue(_should_skip_knowledge_retrieval("Who are you"))

    def test_specific_issues_do_not_skip(self) -> None:
        self.assertFalse(
            _should_skip_knowledge_retrieval(
                "We had bad perforation on PO 4500235940 — what can you do?"
            )
        )
        self.assertFalse(_should_skip_knowledge_retrieval("need 5000 labels 3x4 bopp"))
        self.assertFalse(_should_skip_knowledge_retrieval("what can you do about defective labels?"))


if __name__ == "__main__":
    unittest.main()
