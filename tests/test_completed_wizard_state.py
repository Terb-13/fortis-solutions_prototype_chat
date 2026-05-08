"""Two layers of defense against post-quote wizard re-engagement.

Regression: after a completed quote, ``can you give me a 10% discount?``
caused the bot to start a new wizard ("Step 1/5..."). Root cause was
that ``_resolve_wizard_snap`` fell through to history-based recovery on
"completed" DB sessions, and ``latest_estimate_flow_snapshot`` happily
returned an earlier mid-wizard ``active=True`` snapshot.

This module locks in both fixes:

- Layer A: ``_resolve_wizard_snap`` returns None on status "completed" /
  "abandoned" instead of falling through.
- Layer B: ``latest_estimate_flow_snapshot`` short-circuits via
  ``_wizard_quote_finished_in_history`` before iterating.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch


CID = "550e8400-e29b-41d4-a716-446655440000"


def _midwizard_assistant_msg(pending_idx: int, draft: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": f"Step {pending_idx + 1}/5: ...",
        "meta": {
            "estimate_flow": {
                "active": True,
                "version": 1,
                "pending_step_index": pending_idx,
                "draft": draft,
            }
        },
    }


def _completed_assistant_msg() -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": "### Pricing summary\n\nQuick Ship** estimate has been saved.\n\nQuote: /quote/abc",
        "meta": {
            "estimate_flow": {
                "active": False,
                "version": 1,
                "completed": True,
            }
        },
    }


class TestResolveWizardSnapTerminalStatus(unittest.TestCase):
    """Layer A: terminal DB session status must short-circuit to None."""

    def test_completed_db_session_returns_none(self) -> None:
        from fortis_cs_agent.estimate_flow import _resolve_wizard_snap

        session_row = {
            "status": "completed",
            "current_step": 5,
            "collected_data": {
                "product_details": "5000 3x4 BOPP CMYK gloss",
                "business_name": "Acme",
                "contact_name": "Jane",
                "email": "jane@acme.com",
                "address": "123 Main St",
            },
        }
        # History also has stale active=True snapshots that would be picked
        # up by latest_estimate_flow_snapshot if Layer A didn't short-circuit.
        history = [
            _midwizard_assistant_msg(0, {}),
            _midwizard_assistant_msg(1, {"product_details": "5000 3x4 BOPP CMYK gloss"}),
            _completed_assistant_msg(),
        ]
        result = _resolve_wizard_snap(CID, history, session_row=session_row)
        self.assertIsNone(result)

    def test_abandoned_db_session_returns_none(self) -> None:
        from fortis_cs_agent.estimate_flow import _resolve_wizard_snap

        session_row = {
            "status": "abandoned",
            "current_step": 2,
            "collected_data": {"product_details": "anything"},
        }
        # Stale active=True history that would otherwise be picked up by
        # latest_estimate_flow_snapshot if Layer A didn't short-circuit.
        history = [
            _midwizard_assistant_msg(0, {}),
            _midwizard_assistant_msg(1, {"product_details": "5000 3x4 BOPP"}),
        ]
        result = _resolve_wizard_snap(CID, history, session_row=session_row)
        self.assertIsNone(result)


class TestLatestSnapshotShortCircuitsAfterCompletion(unittest.TestCase):
    """Layer B: history-based snapshot must short-circuit if a quote already
    completed in the thread, even when earlier mid-wizard messages still
    carry active=True meta."""

    def test_history_with_completed_meta_returns_none(self) -> None:
        from fortis_cs_agent.estimate_flow import latest_estimate_flow_snapshot

        history = [
            _midwizard_assistant_msg(0, {}),
            _midwizard_assistant_msg(1, {"product_details": "5000 3x4 BOPP CMYK gloss"}),
            _midwizard_assistant_msg(
                2,
                {
                    "product_details": "5000 3x4 BOPP CMYK gloss",
                    "business_name": "Acme",
                },
            ),
            _completed_assistant_msg(),
        ]
        self.assertIsNone(latest_estimate_flow_snapshot(history))

    def test_history_with_pricing_summary_content_returns_none(self) -> None:
        """Content marker ('### Pricing summary') alone — without completed
        meta — should also be detected as a finished thread."""
        from fortis_cs_agent.estimate_flow import latest_estimate_flow_snapshot

        history = [
            _midwizard_assistant_msg(2, {"product_details": "5000 3x4 BOPP CMYK gloss"}),
            {
                "role": "assistant",
                "content": "### Pricing summary\n\nTotal: $100",
                "meta": None,  # missing meta but content marker is enough
            },
        ]
        self.assertIsNone(latest_estimate_flow_snapshot(history))


class TestDiscountPostQuoteScenario(unittest.IsolatedAsyncioTestCase):
    """Live-transcript regression: post-completed-quote, user says
    'can you give me a 10% discount?'. Must NOT start a new wizard."""

    @patch("fortis_cs_agent.estimate_flow.fetch_estimate_session")
    def test_handle_estimate_flow_returns_unhandled(
        self, fetch_mock: Any
    ) -> None:
        from fortis_cs_agent.estimate_flow import handle_estimate_flow

        # DB session: status="completed" (Layer A defense)
        fetch_mock.return_value = {
            "status": "completed",
            "current_step": 5,
            "collected_data": {},
        }

        # History: completed quote, plus stale mid-wizard snapshots that
        # would have been picked up by the old code (Layer B defense backup)
        history = [
            {"role": "user", "content": "I need 5000 stickers"},
            _midwizard_assistant_msg(0, {}),
            {"role": "user", "content": "5000 3x4 BOPP CMYK gloss"},
            _midwizard_assistant_msg(
                1, {"product_details": "5000 3x4 BOPP CMYK gloss"}
            ),
            {"role": "user", "content": "Acme Corp"},
            _midwizard_assistant_msg(
                2,
                {
                    "product_details": "5000 3x4 BOPP CMYK gloss",
                    "business_name": "Acme Corp",
                },
            ),
            {"role": "user", "content": "Jane Doe"},
            {"role": "user", "content": "jane@acme.com"},
            {"role": "user", "content": "123 Main St"},
            _completed_assistant_msg(),
        ]

        result = handle_estimate_flow(
            user_message="can you give me a 10% discount?",
            conversation_history=history,
            conversation_id=CID,
        )

        # Wizard must NOT re-engage. Goes to Grok / Boundaries logic instead.
        self.assertFalse(result.handled)


if __name__ == "__main__":
    unittest.main()
