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
    ilike = or_ = select
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
