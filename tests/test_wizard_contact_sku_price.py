"""Regressions for estimate b0539f03 / conversation 99189173."""

import unittest
from decimal import Decimal
from unittest.mock import patch

from fortis_cs_agent.estimate_flow import _persist_structured_estimate, handle_estimate_flow
from fortis_cs_agent.knowledge import retrieve_pricing

_CID = "00000000-0000-4000-8000-000000000581"
_NOTE = "roll labels, 3 inch core, no other requirements"
_SPECS = "Price for 2x3 white BOPP labels, quantity 5000; gloss finish, CMYK"
_ROWS = [
    {"sku": "STICKER_1.3438x3.4375_WBGP_G", "width_in": 1.3438, "height_in": 3.4375, "material": "White BOPP"},
    {"sku": "STICKER_2x3_WBGP_G", "width_in": 2, "height_in": 3, "material": "White BOPP", "comment_application": "2x3", "cost_5000": "83.90"},
]

class _Query:
    def __init__(self, rows):
        self.rows, self.n = rows, len(rows)
    def select(self, *_a, **_k):
        return self
    ilike = select
    def or_(self, _filt):
        def _hit(row):
            w, h = float(row["width_in"]), float(row["height_in"])
            return (abs(w - 2) <= 0.02 and abs(h - 3) <= 0.02) or (abs(w - 3) <= 0.02 and abs(h - 2) <= 0.02)
        self.rows = [row for row in self.rows if _hit(row)]
        return self
    def limit(self, n):
        self.n = n
        return self
    def execute(self):
        return type("R", (), {"data": self.rows[: self.n]})()

def _turn(step, draft, message):
    meta = {"estimate_flow": {"active": True, "version": 1, "pending_step_index": step, "draft": draft}}
    return handle_estimate_flow(user_message=message, conversation_history=[{"role": "assistant", "content": "x", "meta": meta}], conversation_id=_CID)

class TestBadEstimateRegressions(unittest.TestCase):
    @patch("fortis_cs_agent.estimate_flow.fetch_estimate_session", return_value=None)
    def test_product_note_and_yes_are_reasked(self, _fetch):
        specs = {"product_details": _SPECS, "_quantity_hint": 5000}
        business = _turn(1, specs, _NOTE)
        draft = business.assistant_meta["estimate_flow"]["draft"]
        self.assertEqual((draft.get("business_name"), draft.get("product_details"), "(again)" in business.reply), (None, _SPECS, True))
        contact = _turn(2, {**specs, "business_name": "Acme Packaging"}, "yes")
        draft = contact.assistant_meta["estimate_flow"]["draft"]
        self.assertEqual((draft.get("contact_name"), draft.get("business_name"), "(again)" in contact.reply), (None, "Acme Packaging", True))

    def test_two_by_three_white_bopp_matches_sku(self):
        client = type("C", (), {"table": lambda self, _name: _Query(_ROWS)})()
        with patch("fortis_cs_agent.knowledge.supabase", client):
            found = retrieve_pricing("Price for 2x3 white BOPP labels, quantity 5000", limit=5)
        self.assertEqual(found[0]["sku"], "STICKER_2x3_WBGP_G")

    def test_qty_times_unit_equals_total(self):
        for cost in ("77.20", "83.90"):
            saved = {}
            def _tool(_name, args, **_k):
                saved["item"] = args["items"][0]
                return {"estimate_id": "00000000-0000-4000-8000-000000000099"}
            row = {**_ROWS[1], "cost_5000": cost}
            with patch("fortis_cs_agent.estimate_flow.retrieve_pricing", return_value=[row]), patch("fortis_cs_agent.estimate_flow.execute_agent_tool", side_effect=_tool):
                reply, _eid = _persist_structured_estimate(conversation_id=_CID, draft={"product_details": _SPECS, "_quantity_hint": 5000, "business_name": "Acme", "contact_name": "Sam Jones", "email": "a@b.co", "address": ""})
            item = saved["item"]
            self.assertEqual(item["quantity"] * item["unit_price"], item["total"])
            self.assertEqual(item["total"], float(Decimal(cost)))
            self.assertIn("Quoted size: 2 × 3 in.", f"{reply}\n{item['description']}")


_ONLY_5000 = {
    "sku": "STICKER_2x3_WBGP_G",
    "width_in": 2,
    "height_in": 3,
    "material": "2.6mil White BOPP / GPX / 40# SCK",
    "finish": "Gloss",
    "comment_application": "2x3 White BOPP - Quick Ship",
    "cost_5000": "83.90",
}


class TestCommaQuantityAndPricingFailure(unittest.TestCase):
    def _finish(self, history: list[dict]) -> tuple[str, dict]:
        saved: dict = {}

        def _tool(_name, args, **_k):
            saved["item"] = args["items"][0]
            return {"estimate_id": "00000000-0000-4000-8000-000000000099"}

        messages = ["Acme Packaging", "Sam Jones", "a@b.co", "skip"]
        result = None
        with (
            patch("fortis_cs_agent.estimate_flow.fetch_estimate_session", return_value=None),
            patch("fortis_cs_agent.estimate_flow.retrieve_pricing", return_value=[_ONLY_5000]),
            patch("fortis_cs_agent.estimate_flow.execute_agent_tool", side_effect=_tool),
        ):
            for message in messages:
                result = handle_estimate_flow(user_message=message, conversation_history=history, conversation_id=_CID)
                history.append({"role": "user", "content": message})
                history.append({"role": "assistant", "content": result.reply, "meta": result.assistant_meta or {}})
        return result.reply or "", saved["item"]

    @patch("fortis_cs_agent.estimate_flow.fetch_estimate_session", return_value=None)
    def test_comma_quantity_prices_at_5000(self, _fetch):
        from fortis_cs_agent.estimate_flow import _extract_quantity

        self.assertEqual(_extract_quantity("5,000"), 5000)
        self.assertEqual(_extract_quantity("5k"), 5000)
        self.assertEqual(_extract_quantity("5 000"), 5000)
        opening = handle_estimate_flow(
            user_message="I need a quote on 5,000 white BOPP labels, 2x3 inches, gloss, CMYK",
            conversation_history=[],
            conversation_id=_CID,
        )
        self.assertEqual(opening.assistant_meta["estimate_flow"]["draft"]["_quantity_hint"], 5000)
        self.assertIn("Step 2/5", opening.reply)
        reply, item = self._finish([
            {"role": "assistant", "content": opening.reply, "meta": opening.assistant_meta},
        ])
        self.assertIn("Your quote is ready.", reply)
        self.assertIn("Size 2 × 3 in", reply)
        self.assertIn("Quantity 5,000", reply)
        self.assertIn("Material White BOPP, gloss", reply)
        self.assertIn("Price $0.01678 each", reply)
        self.assertIn("Total $83.90", reply)
        self.assertIn("Quoted size: 2 × 3 in.", item["description"])
        self.assertEqual(item["quantity"] * item["unit_price"], item["total"])
        self.assertEqual(item["total"], 83.9)
        self.assertNotIn("Could not read a catalog total", reply)
        self.assertNotIn("2.6mil", reply)
        self.assertNotIn("place the order", reply)

        partial = handle_estimate_flow(
            user_message="Quantity is 5,000 labels.",
            conversation_history=_turn_history("white BOPP labels, 2x3 inches, gloss, CMYK"),
            conversation_id=_CID,
        )
        self.assertEqual(partial.assistant_meta["estimate_flow"]["draft"]["_quantity_hint"], 5000)
        reply, item = self._finish([
            {"role": "assistant", "content": partial.reply, "meta": partial.assistant_meta},
        ])
        self.assertEqual(item["total"], 83.9)
        self.assertEqual(item["quantity"], 5000)

    def test_pricing_failure_does_not_restart(self):
        draft = {
            "product_details": "5000 2x3 white bopp gloss cmyk",
            "_quantity_hint": 5000,
            "business_name": "Acme",
            "contact_name": "Sam Jones",
            "email": "a@b.co",
        }
        history = [{"role": "assistant", "content": "**Step 5/5:** address", "meta": {"estimate_flow": {"active": True, "version": 1, "pending_step_index": 4, "draft": draft}}}]
        with (
            patch("fortis_cs_agent.estimate_flow.fetch_estimate_session", return_value=None),
            patch("fortis_cs_agent.estimate_flow.retrieve_pricing", return_value=[{"sku": "STICKER_2x3_WBGP_G", "width_in": 2, "height_in": 3}]),
        ):
            failed = handle_estimate_flow(user_message="skip", conversation_history=history, conversation_id=_CID)
        self.assertEqual(
            failed.reply,
            "I couldn't price that one automatically. Double-check the size and quantity, or ask for a custom quote and include your contact details.",
        )
        self.assertTrue(failed.assistant_meta["estimate_flow"]["completed"])
        self.assertIsNone(failed.estimate_id)
        again = handle_estimate_flow(
            user_message="Acme Packaging",
            conversation_history=[
                {"role": "assistant", "content": "**Step 1/5:** specs"},
                {"role": "user", "content": "5000 2x3 white bopp gloss cmyk"},
                {"role": "assistant", "content": failed.reply},
            ],
            conversation_id=_CID,
        )
        self.assertFalse(again.handled)
        self.assertNotIn("Step 2/5", again.reply)


def _turn_history(product: str) -> list[dict]:
    return [{"role": "assistant", "content": "**Step 1/5:** specs", "meta": {"estimate_flow": {"active": True, "version": 1, "pending_step_index": 0, "draft": {"product_details": product}}}}]

