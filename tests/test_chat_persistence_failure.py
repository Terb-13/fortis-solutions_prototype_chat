"""/chat returns 503 on wizard-state persistence failure (Fix A).

Regression guard: when a wizard-state write to Supabase fails, /chat must
return 503 to the user (so they can retry) and must NOT have already
persisted the user/assistant messages — otherwise the retry would see
inconsistent state.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


CID = "550e8400-e29b-41d4-a716-446655440000"


class TestChatPersistenceFailure(unittest.IsolatedAsyncioTestCase):
    @patch("fortis_cs_agent.api.append_message")
    @patch("fortis_cs_agent.api.load_recent_messages")
    @patch("fortis_cs_agent.api.allocate_web_chat_conversation")
    async def test_sync_failure_returns_503_and_skips_append(
        self,
        alloc: MagicMock,
        load_msgs: MagicMock,
        append: MagicMock,
    ) -> None:
        from fastapi import HTTPException

        from fortis_cs_agent import estimate_flow as _ef
        from fortis_cs_agent.api import ChatRequest, chat
        from fortis_cs_agent.estimate_sessions import EstimateSessionPersistenceError

        alloc.return_value = (CID, True)
        load_msgs.return_value = []

        with patch.object(_ef, "handle_estimate_flow") as handle, patch.object(
            _ef, "sync_wizard_session_from_result"
        ) as sync:
            handle.return_value = _ef.EstimateFlowResult(
                handled=True,
                reply="Step 2 prompt",
                assistant_meta={"estimate_flow": {"active": True, "version": 1}},
                estimate_id=None,
            )
            sync.side_effect = EstimateSessionPersistenceError("simulated upsert failure")

            req = ChatRequest(message="hi")
            res = MagicMock()
            with self.assertRaises(HTTPException) as ctx:
                await chat(req, res)

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("retry", ctx.exception.detail.lower())
        # Mode 1 regression: append_message MUST NOT run when persistence fails.
        append.assert_not_called()

    @patch("fortis_cs_agent.api.append_message")
    @patch("fortis_cs_agent.api.load_recent_messages")
    @patch("fortis_cs_agent.api.allocate_web_chat_conversation")
    async def test_handle_estimate_flow_failure_returns_503(
        self,
        alloc: MagicMock,
        load_msgs: MagicMock,
        append: MagicMock,
    ) -> None:
        """Simulates a failure raised inside handle_estimate_flow (e.g. from
        update_estimate_session_status during topic-shift detection)."""
        from fastapi import HTTPException

        from fortis_cs_agent import estimate_flow as _ef
        from fortis_cs_agent.api import ChatRequest, chat
        from fortis_cs_agent.estimate_sessions import EstimateSessionPersistenceError

        alloc.return_value = (CID, True)
        load_msgs.return_value = []

        with patch.object(_ef, "handle_estimate_flow") as handle:
            handle.side_effect = EstimateSessionPersistenceError("status update failed")

            req = ChatRequest(message="back to the quote")
            res = MagicMock()
            with self.assertRaises(HTTPException) as ctx:
                await chat(req, res)

        self.assertEqual(ctx.exception.status_code, 503)
        append.assert_not_called()

    @patch("fortis_cs_agent.api.append_message")
    @patch("fortis_cs_agent.api.load_recent_messages")
    @patch("fortis_cs_agent.api.allocate_web_chat_conversation")
    async def test_happy_path_appends_both_messages(
        self,
        alloc: MagicMock,
        load_msgs: MagicMock,
        append: MagicMock,
    ) -> None:
        """No regression: a successful wizard turn still persists both messages."""
        from fortis_cs_agent import estimate_flow as _ef
        from fortis_cs_agent.api import ChatRequest, chat

        alloc.return_value = (CID, True)
        load_msgs.return_value = []

        with patch.object(_ef, "handle_estimate_flow") as handle, patch.object(
            _ef, "sync_wizard_session_from_result"
        ) as sync:
            handle.return_value = _ef.EstimateFlowResult(
                handled=True,
                reply="Step 2 prompt",
                assistant_meta={"estimate_flow": {"active": True, "version": 1}},
                estimate_id=None,
            )
            sync.return_value = None

            req = ChatRequest(message="bumper stickers")
            res = MagicMock()
            response = await chat(req, res)

        self.assertEqual(response.reply, "Step 2 prompt")
        self.assertEqual(response.conversation_id, CID)
        # Both the user message and the assistant message persisted.
        self.assertEqual(append.call_count, 2)


if __name__ == "__main__":
    unittest.main()
