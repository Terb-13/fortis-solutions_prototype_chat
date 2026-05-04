"""Tests for POST /chat conversation allocation helpers."""

from __future__ import annotations

import unittest
import uuid
from unittest.mock import MagicMock, patch


class TestAllocateWebChatConversation(unittest.TestCase):
    valid_uuid = "550e8400-e29b-41d4-a716-446655440000"

    @patch("fortis_cs_agent.api._sb")
    def test_no_supabase_returns_persist_false(self, sb: MagicMock) -> None:
        sb.return_value = None

        from fortis_cs_agent.api import allocate_web_chat_conversation

        cid, persist = allocate_web_chat_conversation(None)
        uuid.UUID(cid)
        self.assertFalse(persist)

    @patch.dict(
        "fortis_cs_agent.api.os.environ",
        {"FORTIS_CHAT_RELAX_CONVERSATION_UPSERT": ""},
        clear=False,
    )
    @patch("fortis_cs_agent.api._sb")
    def test_upsert_ok_returns_persist_true(self, sb: MagicMock) -> None:
        sb.return_value = MagicMock()

        chain = sb.return_value
        chain.table.return_value.upsert.return_value.execute.return_value = MagicMock(
            data=[{"id": self.valid_uuid}],
        )

        from fortis_cs_agent.api import allocate_web_chat_conversation

        cid, persist = allocate_web_chat_conversation(self.valid_uuid)
        self.assertEqual(cid, self.valid_uuid)
        self.assertTrue(persist)
        chain.table.return_value.upsert.assert_called_once()

    @patch.dict(
        "fortis_cs_agent.api.os.environ",
        {"FORTIS_CHAT_RELAX_CONVERSATION_UPSERT": ""},
        clear=False,
    )
    @patch("fortis_cs_agent.api._sb")
    def test_upsert_fail_raises_without_relax(self, sb: MagicMock) -> None:
        sb.return_value = MagicMock()
        sb.return_value.table.return_value.upsert.return_value.execute.side_effect = RuntimeError(
            "permission denied for table fortis_conversations"
        )

        from fastapi import HTTPException

        from fortis_cs_agent.api import allocate_web_chat_conversation

        with self.assertRaises(HTTPException) as ctx:
            allocate_web_chat_conversation(self.valid_uuid)
        self.assertEqual(ctx.exception.status_code, 503)
        detail = str(ctx.exception.detail)
        self.assertIn("Conversation store unreachable", detail)
        self.assertIn("fix_fortis_conversations_chat_columns.sql", detail)

    @patch.dict(
        "fortis_cs_agent.api.os.environ",
        {"FORTIS_CHAT_RELAX_CONVERSATION_UPSERT": "1"},
        clear=False,
    )
    @patch("fortis_cs_agent.api._sb")
    def test_upsert_fail_relaxed_returns_persist_false(self, sb: MagicMock) -> None:
        sb.return_value = MagicMock()
        sb.return_value.table.return_value.upsert.return_value.execute.side_effect = RuntimeError("fail")

        from fortis_cs_agent.api import allocate_web_chat_conversation

        cid, persist = allocate_web_chat_conversation(self.valid_uuid)
        self.assertEqual(cid, self.valid_uuid)
        self.assertFalse(persist)


if __name__ == "__main__":
    unittest.main()
