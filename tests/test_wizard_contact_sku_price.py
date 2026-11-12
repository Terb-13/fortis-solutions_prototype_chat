"""Regressions for estimate b0539f03 / conversation 99189173."""

import unittest
from decimal import Decimal
from unittest.mock import patch

from fortis_cs_agent.estimate_flow import _PRICE_UNAVAILABLE, _persist_structured_estimate, handle_estimate_flow
from fortis_cs_agent.knowledge import retrieve_pricing

_CID = "00000000-0000-4000-8000-000000000581"
_NOTE = "roll labels, 3 inch core, no other requirements"
_SPECS = "Price for 2x3 white BOPP labels, quantity 5000; gloss finish, CMYK"
_ROWS = [
    {"sku": "STICKER_1.3438x3.4375_WBGP_G", "width_in": 1.3438, "height_in": 3.4375, "material": "White BOPP"},
    {"sku": "STICKER_2x3_WBGP_G", "width_in": 2, "height_in": 3, "material": "2.6mil White BOPP / GPX / 40# SCK", "finish": "Gloss", "comment_application": "2x3", "cost_5000": "83.90"},
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
            self.assertEqual(item["description"], "2 × 3 in · White BOPP, gloss · CMYK")


def _quantity_misses(n: int):
    history, message, result = [], "I need a quote for 2x3 white bopp gloss cmyk", None
    for _ in range(n):
        result = handle_estimate_flow(user_message=message, conversation_history=history, conversation_id=_CID)
        history += [{"role": "user", "content": message}, {"role": "assistant", "content": result.reply, "meta": result.assistant_meta}]
        message = "I'm not sure yet"
    return result


class TestMidQuoteSizeChange(unittest.TestCase):
    @patch("fortis_cs_agent.estimate_flow.fetch_estimate_session", return_value=None)
    def test_size_change_keeps_the_rest_of_the_message(self, _fetch):
        draft = {"product_details": "2x3 gloss cmyk", "sku": "OLD", "price": 1, "unit_price": 1, "total": 1}
        result = _turn(0, draft, "5000 3x4 white bopp")
        spec = result.assistant_meta["estimate_flow"]["draft"]
        self.assertIn("3x4", spec["product_details"])
        self.assertNotIn("2x3", spec["product_details"])
        self.assertIn("5000", spec["product_details"])
        self.assertIn("bopp", spec["product_details"])
        self.assertEqual(spec["_quantity_hint"], 5000)
        self.assertFalse(any(k in spec for k in ("sku", "price", "unit_price", "total")))

    @patch("fortis_cs_agent.estimate_flow.fetch_estimate_session", return_value=None)
    def test_quantity_nags_reset_after_cancel(self, _fetch):
        history, message, result = [], "I need a quote for 2x3 white bopp gloss cmyk", None
        for _ in range(4):
            result = handle_estimate_flow(user_message=message, conversation_history=history, conversation_id=_CID)
            history += [{"role": "user", "content": message}, {"role": "assistant", "content": result.reply, "meta": result.assistant_meta}]
            message = "I'm not sure yet"
        cancelled = handle_estimate_flow(user_message="cancel", conversation_history=history, conversation_id=_CID)
        history += [{"role": "user", "content": "cancel"}, {"role": "assistant", "content": cancelled.reply, "meta": cancelled.assistant_meta}]
        fresh = handle_estimate_flow(user_message="I need a quote for 2x3 white bopp gloss cmyk", conversation_history=history, conversation_id=_CID)
        self.assertIn("**quantity**", fresh.reply)
        self.assertIn("still need", fresh.reply.lower())

    @patch("fortis_cs_agent.estimate_flow.upsert_estimate_session")
    @patch("fortis_cs_agent.estimate_flow.fetch_estimate_session")
    def test_size_change_is_saved_when_nags_are_exhausted(self, fetch, upsert):
        fetch.return_value = {"status": "in_progress", "collected_data": {"product_details": "2x3 gloss cmyk"}}
        history = []
        for text in (
            "**Step 1/5:** Thanks — I still need: **quantity**.",
            "No problem. Most orders start at 1,000 labels. Send a number to price it.",
            "No problem. Whenever you have a number, send it here and I'll price it.",
        ):
            history += [{"role": "user", "content": "not sure"}, {"role": "assistant", "content": text, "meta": {"estimate_flow": {"active": True, "version": 1, "pending_step_index": 0, "draft": {"product_details": "2x3 gloss cmyk"}}}}]
        result = handle_estimate_flow(user_message="3x4 white bopp", conversation_history=history, conversation_id=_CID)
        self.assertFalse(result.handled)
        saved = upsert.call_args.kwargs["collected_data"]["product_details"]
        self.assertIn("3x4", saved)
        self.assertIn("bopp", saved)
        self.assertNotIn("2x3", saved)

    @patch("fortis_cs_agent.estimate_flow.fetch_estimate_session", return_value=None)
    def test_third_quantity_miss(self, _fetch):
        self.assertEqual(_quantity_misses(3).reply, "No problem. Whenever you have a number, send it here and I'll price it.")

    @patch("fortis_cs_agent.estimate_flow.fetch_estimate_session", return_value=None)
    def test_fourth_quantity_miss_is_silent(self, _fetch):
        result = _quantity_misses(4)
        self.assertFalse(result.handled)
        self.assertEqual(result.reply, "")

    @patch("fortis_cs_agent.estimate_flow.fetch_estimate_session", return_value=None)
    def test_custom_quote_without_session(self, _fetch):
        result = handle_estimate_flow(user_message="Can someone help me with a custom quote?", conversation_history=[], conversation_id=_CID)
        self.assertEqual(result.reply, "Sure. Send the size, quantity, material and finish you need.")
        self.assertNotIn("Step 1/5", result.reply)


class TestSecondQuantityMiss(unittest.TestCase):
    @patch("fortis_cs_agent.estimate_flow.fetch_estimate_session", return_value=None)
    def test_second_miss_does_not_repeat_step1(self, _fetch):
        first = handle_estimate_flow(
            user_message="I need a quote for 2x3 white bopp gloss cmyk",
            conversation_history=[],
            conversation_id=_CID,
        )
        self.assertIn("**quantity**", first.reply)
        second = handle_estimate_flow(
            user_message="I'm not sure yet",
            conversation_history=[{"role": "assistant", "content": first.reply, "meta": first.assistant_meta}],
            conversation_id=_CID,
        )
        self.assertEqual(
            second.reply,
            "No problem. Most orders start at 1,000 labels. Send a number to price it.",
        )
        self.assertNotEqual(second.reply.strip(), first.reply.strip())


class TestCommaQuantityAndPricingFailure(unittest.TestCase):
    @patch("fortis_cs_agent.estimate_flow.fetch_estimate_session", return_value=None)
    @patch("fortis_cs_agent.estimate_flow.retrieve_pricing", return_value=[_ROWS[1]])
    @patch("fortis_cs_agent.estimate_flow.execute_agent_tool")
    def test_comma_quantity_prices_at_5000(self, tool, _pricing, _fetch):
        from fortis_cs_agent.knowledge import _extract_quantity

        self.assertEqual(_extract_quantity("5,000"), 5000)
        self.assertEqual(_extract_quantity("5k"), 5000)
        self.assertEqual(_extract_quantity("5 000"), 5000)
        saved = {}
        tool.side_effect = lambda _n, args, **_k: saved.update(item=args["items"][0]) or {"estimate_id": "00000000-0000-4000-8000-000000000099"}
        history = []
        for message in ("I need a quote on 5,000 white BOPP labels, 2x3 inches, gloss, CMYK", "Acme Packaging", "Sam Jones", "a@b.co", "skip"):
            result = handle_estimate_flow(user_message=message, conversation_history=history, conversation_id=_CID)
            history += [{"role": "user", "content": message}, {"role": "assistant", "content": result.reply, "meta": result.assistant_meta or {}}]
        item = saved["item"]
        self.assertEqual(item["total"], 83.9)
        self.assertEqual(item["quantity"] * item["unit_price"], item["total"])

    def test_pricing_failure_does_not_restart(self):
        from fortis_cs_agent.estimate_flow import _PRICE_UNAVAILABLE

        draft = {"product_details": "5000 2x3 white bopp gloss cmyk", "_quantity_hint": 5000, "business_name": "Acme", "contact_name": "Sam Jones", "email": "a@b.co"}
        history = [{"role": "assistant", "content": "**Step 5/5:** address", "meta": {"estimate_flow": {"active": True, "version": 1, "pending_step_index": 4, "draft": draft}}}]
        with patch("fortis_cs_agent.estimate_flow.fetch_estimate_session", return_value=None), patch("fortis_cs_agent.estimate_flow.retrieve_pricing", return_value=[{"sku": "X"}]):
            failed = handle_estimate_flow(user_message="skip", conversation_history=history, conversation_id=_CID)
        self.assertIn(_PRICE_UNAVAILABLE, failed.reply)
        again = handle_estimate_flow(user_message="Acme Packaging", conversation_history=[{"role": "assistant", "content": "**Step 1/5:** specs"}, {"role": "user", "content": "5000 2x3 white bopp gloss cmyk"}, {"role": "assistant", "content": failed.reply}], conversation_id=_CID)
        self.assertFalse(again.handled)
        self.assertNotIn("Step 2/5", again.reply)


def _skip_quote(product: str, hint: int, rows: list[dict]):
    draft = {"product_details": product, "_quantity_hint": hint, "business_name": "Acme", "contact_name": "Sam Jones", "email": "a@b.co"}
    history = [{"role": "assistant", "content": "**Step 5/5:** address", "meta": {"estimate_flow": {"active": True, "version": 1, "pending_step_index": 4, "draft": draft}}}]
    with (
        patch("fortis_cs_agent.estimate_flow.fetch_estimate_session", return_value=None),
        patch("fortis_cs_agent.estimate_flow.retrieve_pricing", return_value=rows),
        patch("fortis_cs_agent.estimate_flow.execute_agent_tool") as tool,
    ):
        result = handle_estimate_flow(user_message="skip", conversation_history=history, conversation_id=_CID)
    return result, tool


class TestUnlistedSizeAndBelowTier(unittest.TestCase):
    def test_unlisted_7x11_is_not_quoted(self):
        result, tool = _skip_quote("5000 labels 7x11 white bopp gloss cmyk", 5000, [])
        self.assertIn(_PRICE_UNAVAILABLE, result.reply)
        self.assertIsNone(result.estimate_id)
        tool.assert_not_called()

    def test_quantity_below_smallest_tier_is_not_quoted(self):
        result, tool = _skip_quote("37 labels 2x3 white bopp gloss cmyk", 37, [{**_ROWS[1], "cost_100": "3091.10"}])
        self.assertIn(_PRICE_UNAVAILABLE, result.reply)
        self.assertIsNone(result.estimate_id)
        tool.assert_not_called()


