"""Tests for conversation persistence helpers in fortis_cs_agent.api."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import unittest


class TestEnsureConversation(unittest.TestCase):
    """ensure_conversation must upsert even when the client sends an existing_id (FK safety)."""

    def setUp(self) -> None:
        self.valid_uuid = "550e8400-e29b-41d4-a716-446655440000"

    def _mock_chain(self) -> MagicMock:
        client = MagicMock()
        execute_result = MagicMock()
        execute_result.data = [{"id": self.valid_uuid}]
        client.table.return_value.upsert.return_value.execute.return_value = execute_result
        return client

    @patch("fortis_cs_agent.api._sb")
    def test_no_db_returns_normalized_or_new_uuid(self, sb: MagicMock) -> None:
        sb.return_value = None
        from fortis_cs_agent.api import ensure_conversation

        cid = ensure_conversation(channel="web", channel_ref=None, existing_id=None)
        self.assertIsNotNone(cid)
        uuid.UUID(cid)

        cid2 = ensure_conversation(channel="web", channel_ref=None, existing_id=self.valid_uuid)
        self.assertEqual(cid2, self.valid_uuid)

    @patch("fortis_cs_agent.api._sb")
    def test_existing_id_still_upserts_row(self, sb: MagicMock) -> None:
        """Regression: previously returned existing_id without insert → FK errors on messages."""
        client = self._mock_chain()
        sb.return_value = client

        from fortis_cs_agent.api import CONV_TABLE, ensure_conversation

        cid = ensure_conversation(
            channel="web",
            channel_ref=None,
            existing_id=self.valid_uuid,
        )
        self.assertEqual(cid, self.valid_uuid)
        client.table.assert_called_with(CONV_TABLE)
        client.table.return_value.upsert.assert_called_once_with(
            [
                {
                    "id": self.valid_uuid,
                    "channel": "web",
                    "channel_ref": "",
                },
            ],
            on_conflict="id",
            default_to_null=False,
        )

    @patch("fortis_cs_agent.api._sb")
    def test_first_message_allocates_id_and_upserts(self, sb: MagicMock) -> None:
        client = self._mock_chain()
        sb.return_value = client

        from fortis_cs_agent.api import ensure_conversation

        cid = ensure_conversation(channel="web", channel_ref=None, existing_id=None)
        uuid.UUID(cid)
        client.table.return_value.upsert.assert_called_once()
        rows = client.table.return_value.upsert.call_args[0][0]
        row = rows[0]
        self.assertEqual(row["id"], cid)
        self.assertEqual(row["channel"], "web")

    @patch("fortis_cs_agent.api._sb")
    def test_invalid_existing_id_replaced_and_upserted(self, sb: MagicMock) -> None:
        client = self._mock_chain()
        sb.return_value = client

        from fortis_cs_agent.api import ensure_conversation

        cid = ensure_conversation(
            channel="web",
            channel_ref=None,
            existing_id="not-a-real-uuid",
        )
        self.assertNotEqual(cid, "not-a-real-uuid")
        uuid.UUID(cid)
        client.table.return_value.upsert.assert_called_once()
        args_row = client.table.return_value.upsert.call_args[0][0]
        self.assertEqual(args_row[0]["id"], cid)

    @patch("fortis_cs_agent.api._sb")
    def test_upsert_failure_returns_none(self, sb: MagicMock) -> None:
        client = MagicMock()
        client.table.return_value.upsert.return_value.execute.side_effect = RuntimeError(
            "permission denied for table fortis_conversations"
        )
        sb.return_value = client

        from fortis_cs_agent.api import ensure_conversation

        cid = ensure_conversation(channel="web", channel_ref=None, existing_id=None)
        self.assertIsNone(cid)


if __name__ == "__main__":
    unittest.main()
