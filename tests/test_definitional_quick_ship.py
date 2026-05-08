"""Definitional Quick Ship questions must not be classified as estimate
requests, and must trigger topic-shift mid-wizard.

Regression: a user mid-wizard asking *"actually wait, what's a quick ship
order?"* was previously treated as a wizard step answer because the bare
phrase "quick ship" matched ``_STRICT_KEYWORD_RE`` inside
``is_estimate_request``. The fix adds ``_DEFINITIONAL_QUICK_SHIP_RE`` and
threads it through both ``is_estimate_request`` and
``should_exit_estimate_wizard_for_topic_shift``.
"""

from __future__ import annotations

import unittest


class TestDefinitionalQuickShipNotEstimate(unittest.TestCase):
    def test_what_is_a_quick_ship_order_not_estimate(self) -> None:
        from fortis_cs_agent.estimate_detector import is_estimate_request

        self.assertFalse(is_estimate_request("what is a quick ship order?"))

    def test_whats_quick_ship_not_estimate(self) -> None:
        from fortis_cs_agent.estimate_detector import is_estimate_request

        self.assertFalse(is_estimate_request("what's quick ship?"))

    def test_tell_me_about_quick_ship_not_estimate(self) -> None:
        from fortis_cs_agent.estimate_detector import is_estimate_request

        self.assertFalse(is_estimate_request("tell me about quick ship"))

    def test_explain_quick_ship_not_estimate(self) -> None:
        from fortis_cs_agent.estimate_detector import is_estimate_request

        self.assertFalse(is_estimate_request("explain quick ship"))


class TestQuickShipQuoteStillEstimateRequest(unittest.TestCase):
    """Regression guards: real quote-buying phrasings with 'quick ship'
    must continue to classify as estimate requests."""

    def test_i_want_a_quick_ship_quote(self) -> None:
        from fortis_cs_agent.estimate_detector import is_estimate_request

        self.assertTrue(is_estimate_request("I want a quick ship quote"))

    def test_5000_quick_ship_labels(self) -> None:
        from fortis_cs_agent.estimate_detector import is_estimate_request

        self.assertTrue(is_estimate_request("5000 quick ship labels"))

    def test_whats_the_price_for_quick_ship_still_estimate(self) -> None:
        """Definitional shape ('what's...') with explicit pricing word ('price')
        must still be an estimate request — falls through to _STRICT_KEYWORD_RE
        on 'price'."""
        from fortis_cs_agent.estimate_detector import is_estimate_request

        self.assertTrue(is_estimate_request("what's the price for quick ship?"))


class TestTopicShiftPausesForDefinitionalQuickShip(unittest.TestCase):
    """The exact live-transcript scenario: mid-wizard, user pivots to ask
    'actually wait, what's a quick ship order?'. Must return True so the
    wizard pauses and the question routes to Grok."""

    def test_actually_wait_whats_a_quick_ship_order(self) -> None:
        from fortis_cs_agent.estimate_detector import (
            should_exit_estimate_wizard_for_topic_shift,
        )

        self.assertTrue(
            should_exit_estimate_wizard_for_topic_shift(
                "actually wait, what's a quick ship order?"
            )
        )


if __name__ == "__main__":
    unittest.main()
