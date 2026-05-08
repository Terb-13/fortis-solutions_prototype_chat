"""run_agent_turn must invoke both retrievers and merge results into the
prompt, and must NOT inject an internal-reference block when both retrievers
return empty.

The Grok call is mocked — we assert on the augmented user message that
would have been sent to the model.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class TestRunAgentTurnKnowledge(unittest.IsolatedAsyncioTestCase):
    @patch("fortis_cs_agent.api._grok_chat", new_callable=AsyncMock)
    @patch("fortis_cs_agent.api.retrieve_faq")
    @patch("fortis_cs_agent.api.retrieve_knowledge")
    @patch("fortis_cs_agent.api.retrieve_pricing")
    @patch("fortis_cs_agent.api.load_recent_messages")
    async def test_run_agent_turn_includes_both_retrievers(
        self,
        load_msgs: MagicMock,
        ret_pricing: MagicMock,
        ret_kn: MagicMock,
        ret_faq: MagicMock,
        grok: AsyncMock,
    ) -> None:
        from fortis_cs_agent.api import run_agent_turn

        load_msgs.return_value = []
        ret_pricing.return_value = []
        ret_kn.return_value = [
            {
                "id": "k1",
                "title": "SBU update",
                "content": "Fortis Edge SBU launched as a digital-speed business unit.",
                "category": "sbu",
                "source": "knowledge",
            },
        ]
        ret_faq.return_value = [
            {
                "id": "f1",
                "question": "What is the SBU?",
                "answer": "Fortis Edge serves Tier 3/4 customers.",
                "category": "general",
                "published": True,
            },
        ]
        grok.return_value = {"choices": [{"message": {"content": "Test reply"}}]}

        await run_agent_turn("tell me about the sbu", conversation_id="cid-1")

        ret_kn.assert_called_once()
        ret_faq.assert_called_once()

        grok_msgs = grok.call_args.args[0]
        last_user_msg = grok_msgs[-1]["content"]

        # FAQ entry is labeled so the prompt's facts-faithful rule can find it.
        self.assertIn("[FAQ]", last_user_msg)
        self.assertIn("Fortis Edge serves Tier 3/4 customers.", last_user_msg)
        # Knowledge content also flows through.
        self.assertIn("Fortis Edge SBU launched as a digital-speed business unit.", last_user_msg)
        # Reference block delimiters present.
        self.assertIn("--- internal reference ---", last_user_msg)
        self.assertIn("Customer message:", last_user_msg)

    @patch("fortis_cs_agent.api._grok_chat", new_callable=AsyncMock)
    @patch("fortis_cs_agent.api.retrieve_faq")
    @patch("fortis_cs_agent.api.retrieve_knowledge")
    @patch("fortis_cs_agent.api.retrieve_pricing")
    @patch("fortis_cs_agent.api.load_recent_messages")
    async def test_no_results_means_no_reference_block(
        self,
        load_msgs: MagicMock,
        ret_pricing: MagicMock,
        ret_kn: MagicMock,
        ret_faq: MagicMock,
        grok: AsyncMock,
    ) -> None:
        """When both retrievers return empty, the model must NOT see an
        internal-reference block — the prompt's 'no coverage' rule then
        produces the customer-friendly fallback."""
        from fortis_cs_agent.api import run_agent_turn

        load_msgs.return_value = []
        ret_pricing.return_value = []
        ret_kn.return_value = []
        ret_faq.return_value = []
        grok.return_value = {"choices": [{"message": {"content": "Test reply"}}]}

        await run_agent_turn("tell me about something obscure", conversation_id="cid-1")

        grok_msgs = grok.call_args.args[0]
        last_user_msg = grok_msgs[-1]["content"]

        self.assertNotIn("--- internal reference ---", last_user_msg)
        self.assertNotIn("[FAQ]", last_user_msg)
        # User message went through unaugmented.
        self.assertEqual(last_user_msg, "tell me about something obscure")


if __name__ == "__main__":
    unittest.main()
