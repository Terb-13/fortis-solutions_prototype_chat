"""Wizard-state persistence: write failures must surface, not silently swallow.

Locks in the contract that ``upsert_estimate_session`` and
``update_estimate_session_status`` raise ``EstimateSessionPersistenceError``
on Supabase failure, while preserving the no-Supabase (dev/test) early
return.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


CID = "550e8400-e29b-41d4-a716-446655440000"


class TestUpsertEstimateSession(unittest.TestCase):
    @patch("fortis_cs_agent.estimate_sessions._client")
    def test_no_client_returns_silently(self, client: MagicMock) -> None:
        client.return_value = None
        from fortis_cs_agent.estimate_sessions import upsert_estimate_session

        result = upsert_estimate_session(
            CID,
            current_step=1,
            collected_data={},
            status="in_progress",
        )
        self.assertIsNone(result)

    @patch("fortis_cs_agent.estimate_sessions._client")
    def test_blank_conversation_id_returns_silently(self, client: MagicMock) -> None:
        client.return_value = MagicMock()
        from fortis_cs_agent.estimate_sessions import upsert_estimate_session

        result = upsert_estimate_session(
            "",
            current_step=1,
            collected_data={},
            status="in_progress",
        )
        self.assertIsNone(result)

    @patch("fortis_cs_agent.estimate_sessions._client")
    def test_supabase_failure_raises_persistence_error(self, client: MagicMock) -> None:
        sb = MagicMock()
        sb.table.return_value.upsert.return_value.execute.side_effect = RuntimeError("boom")
        client.return_value = sb

        from fortis_cs_agent.estimate_sessions import (
            EstimateSessionPersistenceError,
            upsert_estimate_session,
        )

        with self.assertRaises(EstimateSessionPersistenceError) as ctx:
            upsert_estimate_session(
                CID,
                current_step=2,
                collected_data={"product_details": "x"},
                status="in_progress",
            )
        # Original cause preserved for log diagnostics.
        self.assertIsInstance(ctx.exception.__cause__, RuntimeError)


class TestUpdateEstimateSessionStatus(unittest.TestCase):
    @patch("fortis_cs_agent.estimate_sessions._client")
    def test_no_client_returns_silently(self, client: MagicMock) -> None:
        client.return_value = None
        from fortis_cs_agent.estimate_sessions import update_estimate_session_status

        result = update_estimate_session_status(CID, "paused")
        self.assertIsNone(result)

    @patch("fortis_cs_agent.estimate_sessions._client")
    def test_blank_conversation_id_returns_silently(self, client: MagicMock) -> None:
        client.return_value = MagicMock()
        from fortis_cs_agent.estimate_sessions import update_estimate_session_status

        result = update_estimate_session_status("   ", "paused")
        self.assertIsNone(result)

    @patch("fortis_cs_agent.estimate_sessions._client")
    def test_supabase_failure_raises_persistence_error(self, client: MagicMock) -> None:
        sb = MagicMock()
        sb.table.return_value.update.return_value.eq.return_value.execute.side_effect = (
            RuntimeError("postgrest down")
        )
        client.return_value = sb

        from fortis_cs_agent.estimate_sessions import (
            EstimateSessionPersistenceError,
            update_estimate_session_status,
        )

        with self.assertRaises(EstimateSessionPersistenceError) as ctx:
            update_estimate_session_status(CID, "paused")
        self.assertIsInstance(ctx.exception.__cause__, RuntimeError)


if __name__ == "__main__":
    unittest.main()
