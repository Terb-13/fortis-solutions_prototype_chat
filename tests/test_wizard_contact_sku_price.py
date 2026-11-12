"""Regressions from estimate b0539f03-85b1-4926-a944-6686a6c0e973.

Conversation 99189173 (Fortis Prototype, read-only):

- "Price for 2x3 white BOPP labels, quantity 5000"
- "gloss finish, CMYK"
- "roll labels, 3 inch core, no other requirements"  → stored as business_name
- "yes"  → stored as contact_name
- email, then address skip

The saved line was STICKER_1.3438x3.4375_WBGP_G at unit_price 0.0154
with total 77.20 (5,000 × 0.01544). fortis_pricing has STICKER_2x3_WBGP_G.
"""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import patch

from fortis_cs_agent.estimate_flow import (
    _persist_structured_estimate,
    handle_estimate_flow,
    unit_price_matching_total,
)
from fortis_cs_agent.knowledge import retrieve_pricing

_CID = "00000000-0000-4000-8000-000000000581"
_PRODUCT = "roll labels, 3 inch core, no other requirements"
_NARROW = {
    "id": 1,
    "sku": "STICKER_1.3438x3.4375_WBGP_G",
    "width_in": 1.3438,
    "height_in": 3.4375,
    "material": "2.6mil White BOPP / GPX / 40# SCK",
    "finish": "Gloss",
    "comment_application": "Narrow no-label-look",
    "cost_5000": "77.2",
}
_MATTE_2X3 = {
    "id": 50,
    "sku": "STICKER_2x3_AAA_M",
    "width_in": 2,
    "height_in": 3,
    "material": "2.6mil White BOPP / GPX / 40# SCK",
    "finish": "Matte",
    "comment_application": "2x3 White BOPP matte",
    "cost_5000": "80.00",
}
_GLOSS_2X3 = {
    "id": 51,
    "sku": "STICKER_2x3_WBGP_G",
    "width_in": 2,
    "height_in": 3,
    "material": "2.6mil White BOPP / GPX / 40# SCK",
    "finish": "Gloss",
    "comment_application": "2x3 White BOPP - Quick Ship",
    "cost_5000": "83.90",
}


def _filler_bopp(idx: int) -> dict:
    return {
        "id": 100 + idx,
        "sku": f"STICKER_8x3_BOPP_{idx}",
        "width_in": 8,
        "height_in": 3,
        "material": "2.6mil White BOPP / GPX / 40# SCK",
        "finish": "Gloss",
        "comment_application": "filler",
        "cost_5000": "100",
    }


class _Result:
    def __init__(self, data: list) -> None:
        self.data = data


class _Query:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self._preds: list = []
        self._limit: int | None = None
        self._ilike: tuple[str, str] | None = None

    def select(self, *_args: object, **_kwargs: object) -> "_Query":
        return self

    def ilike(self, col: str, pattern: str) -> "_Query":
        self._ilike = (col, pattern.strip("%").lower())
        return self

    def gte(self, col: str, val: float) -> "_Query":
        ceiling = float(val)

        def _pred(row: dict, column: str = col, bound: float = ceiling) -> bool:
            number = _as_float(row.get(column))
            return number is not None and number >= bound - 1e-9

        self._preds.append(_pred)
        return self

    def lte(self, col: str, val: float) -> "_Query":
        ceiling = float(val)

        def _pred(row: dict, column: str = col, bound: float = ceiling) -> bool:
            number = _as_float(row.get(column))
            return number is not None and number <= bound + 1e-9

        self._preds.append(_pred)
        return self

    def limit(self, n: int) -> "_Query":
        self._limit = n
        return self

    def execute(self) -> _Result:
        matched: list[dict] = []
        for row in self._rows:
            if self._ilike is not None:
                col, needle = self._ilike
                if needle not in str(row.get(col) or "").lower():
                    continue
            if all(pred(row) for pred in self._preds):
                matched.append(row)
        if self._limit is not None:
            matched = matched[: self._limit]
        return _Result(matched)


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class _Client:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def table(self, _name: str) -> _Query:
        return _Query(self._rows)


def _draft_of(result) -> dict:
    assert result.assistant_meta is not None
    draft = result.assistant_meta["estimate_flow"]["draft"]
    assert isinstance(draft, dict)
    return draft


class TestContactFieldsReplay(unittest.TestCase):
    """Replay the turns that wrote the bad contact fields."""

    def _turn(self, history: list[dict], message: str):
        result = handle_estimate_flow(
            user_message=message,
            conversation_history=history,
            conversation_id=_CID,
        )
        history.append({"role": "user", "content": message})
        history.append(
            {
                "role": "assistant",
                "content": result.reply,
                "meta": result.assistant_meta or {},
            }
        )
        return result

    @patch("fortis_cs_agent.estimate_flow.fetch_estimate_session", return_value=None)
    def test_replay_does_not_store_product_note_or_yes(self, _fetch) -> None:
        history: list[dict] = []
        opener = self._turn(history, "Hi, I need labels")
        self.assertFalse(opener.handled)

        specs = self._turn(history, "Price for 2x3 white BOPP labels, quantity 5000")
        self.assertTrue(specs.handled)
        self.assertIn("Step 1/5", specs.reply)

        finish = self._turn(history, "gloss finish, CMYK")
        self.assertIn("Step 2/5", finish.reply)
        self.assertIn("business name", finish.reply.lower())

        requirements = self._turn(history, _PRODUCT)
        draft = _draft_of(requirements)
        self.assertEqual(requirements.assistant_meta["estimate_flow"]["pending_step_index"], 1)
        self.assertIsNone(draft.get("business_name"))
        self.assertIsNone(draft.get("contact_name"))
        self.assertNotEqual(draft.get("business_name"), _PRODUCT)
        self.assertNotEqual(draft.get("contact_name"), _PRODUCT)
        self.assertIn("3 inch core", str(draft.get("product_details") or "").lower())
        self.assertIn("2x3", str(draft.get("product_details") or "").lower())
        self.assertIn("Step 2/5 (again)", requirements.reply)

        yes = self._turn(history, "yes")
        draft = _draft_of(yes)
        self.assertEqual(yes.assistant_meta["estimate_flow"]["pending_step_index"], 1)
        self.assertIsNone(draft.get("business_name"))
        self.assertIsNone(draft.get("contact_name"))
        self.assertNotEqual((draft.get("business_name") or "").lower(), "yes")
        self.assertNotEqual((draft.get("contact_name") or "").lower(), "yes")

        business = self._turn(history, "Acme Packaging")
        draft = _draft_of(business)
        self.assertEqual(draft.get("business_name"), "Acme Packaging")
        self.assertEqual(business.assistant_meta["estimate_flow"]["pending_step_index"], 2)

        yes_contact = self._turn(history, "yes")
        draft = _draft_of(yes_contact)
        self.assertEqual(yes_contact.assistant_meta["estimate_flow"]["pending_step_index"], 2)
        self.assertIsNone(draft.get("contact_name"))
        self.assertEqual(draft.get("business_name"), "Acme Packaging")

        contact = self._turn(history, "Sam Jones")
        draft = _draft_of(contact)
        self.assertEqual(draft.get("contact_name"), "Sam Jones")
        self.assertEqual(draft.get("business_name"), "Acme Packaging")
        self.assertNotIn(_PRODUCT, (draft.get("business_name"), draft.get("contact_name")))

    @patch("fortis_cs_agent.estimate_flow.fetch_estimate_session", return_value=None)
    def test_product_note_on_contact_step_stays_out_of_contact_name(self, _fetch) -> None:
        history = [
            {
                "role": "assistant",
                "content": "**Step 3/5:** What’s the **contact name** for this order?",
                "meta": {
                    "estimate_flow": {
                        "active": True,
                        "version": 1,
                        "pending_step_index": 2,
                        "draft": {
                            "product_details": "5000 labels 2x3 in white bopp gloss cmyk",
                            "_quantity_hint": 5000,
                            "business_name": "Acme Packaging",
                        },
                    }
                },
            }
        ]
        result = handle_estimate_flow(
            user_message=_PRODUCT,
            conversation_history=history,
            conversation_id=_CID,
        )
        draft = _draft_of(result)
        self.assertEqual(result.assistant_meta["estimate_flow"]["pending_step_index"], 2)
        self.assertIsNone(draft.get("contact_name"))
        self.assertEqual(draft.get("business_name"), "Acme Packaging")
        self.assertIn("3 inch core", draft["product_details"].lower())

    @patch("fortis_cs_agent.estimate_flow.fetch_estimate_session", return_value=None)
    def test_company_shaped_label_name_still_accepted(self, _fetch) -> None:
        history = [
            {
                "role": "assistant",
                "content": "**Step 2/5:** business name",
                "meta": {
                    "estimate_flow": {
                        "active": True,
                        "version": 1,
                        "pending_step_index": 1,
                        "draft": {
                            "product_details": "5000 labels 2x3 in white bopp gloss cmyk",
                            "_quantity_hint": 5000,
                        },
                    }
                },
            }
        ]
        result = handle_estimate_flow(
            user_message="Roll Labels Co",
            conversation_history=history,
            conversation_id=_CID,
        )
        draft = _draft_of(result)
        self.assertEqual(draft.get("business_name"), "Roll Labels Co")
        self.assertEqual(result.assistant_meta["estimate_flow"]["pending_step_index"], 2)


class TestSizeToSku(unittest.TestCase):
    def test_two_by_three_request_lands_on_two_by_three_sku(self) -> None:
        rows = [_NARROW, *(_filler_bopp(i) for i in range(12)), _MATTE_2X3, _GLOSS_2X3]
        client = _Client(rows)
        with patch("fortis_cs_agent.knowledge.supabase", client):
            found = retrieve_pricing(
                "Price for 2x3 white BOPP labels, quantity 5000; gloss finish, CMYK",
                limit=5,
            )
        self.assertTrue(found)
        self.assertEqual(found[0]["sku"], "STICKER_2x3_WBGP_G")
        self.assertEqual(found[0]["_fortis_pricing_match"], "exact_size")
        self.assertNotEqual(found[0]["sku"], "STICKER_1.3438x3.4375_WBGP_G")

    def test_material_query_without_size_still_returns_catalog_rows(self) -> None:
        rows = [_NARROW, _GLOSS_2X3]
        client = _Client(rows)
        with patch("fortis_cs_agent.knowledge.supabase", client):
            found = retrieve_pricing("white bopp gloss labels", limit=5)
        self.assertTrue(found)
        self.assertIn("BOPP", found[0]["material"].upper())
        self.assertEqual(found[0]["_fortis_pricing_match"], "material_match")

    def test_missing_exact_size_is_labeled_as_nearest(self) -> None:
        rows = [_NARROW, *(_filler_bopp(i) for i in range(4))]
        client = _Client(rows)
        with patch("fortis_cs_agent.knowledge.supabase", client):
            found = retrieve_pricing("Price for 2x3 white BOPP labels, quantity 5000", limit=5)
        self.assertTrue(found)
        self.assertNotEqual(found[0]["sku"], "STICKER_2x3_WBGP_G")
        self.assertAlmostEqual(float(found[0]["width_in"]), 1.3438)
        self.assertIn(found[0]["_fortis_pricing_match"], {"closest_size", "closest_fallback"})


class TestQuotedSizeAndUnitPrice(unittest.TestCase):
    def _save(self, row: dict, blob: str):
        captured: dict = {}

        def _tool(name, args, **kwargs):
            captured["name"] = name
            captured["args"] = args
            return {"estimate_id": "00000000-0000-4000-8000-000000000099"}

        with (
            patch("fortis_cs_agent.estimate_flow.retrieve_pricing", return_value=[row]),
            patch("fortis_cs_agent.estimate_flow.execute_agent_tool", side_effect=_tool),
        ):
            reply, estimate_id = _persist_structured_estimate(
                conversation_id=_CID,
                draft={
                    "product_details": blob,
                    "_quantity_hint": 5000,
                    "business_name": "Acme Packaging",
                    "contact_name": "Sam Jones",
                    "email": "rls-test@example.com",
                    "address": "",
                },
            )
        self.assertEqual(estimate_id, "00000000-0000-4000-8000-000000000099")
        self.assertEqual(captured["name"], "create_estimate")
        return reply or "", captured["args"]["items"][0]

    def test_substitution_names_the_quoted_size(self) -> None:
        row = {**_NARROW, "_fortis_pricing_match": "closest_size"}
        reply, item = self._save(
            row,
            "Price for 2x3 white BOPP labels, quantity 5000; gloss finish, CMYK",
        )
        self.assertIn("1.3438 × 3.4375 in.", reply)
        self.assertIn("2 × 3 in.", reply)
        self.assertIn("1.3438 × 3.4375 in.", item["description"])
        self.assertIn("2 × 3 in.", item["description"])
        self.assertIn("nearest Quick Ship size", item["description"])

    def test_exact_two_by_three_quote_states_that_size(self) -> None:
        row = {**_GLOSS_2X3, "_fortis_pricing_match": "exact_size"}
        reply, item = self._save(
            row,
            "Price for 2x3 white BOPP labels, quantity 5000; gloss finish, CMYK",
        )
        self.assertIn("STICKER_2x3_WBGP_G", reply)
        self.assertIn("2 × 3 in.", reply)
        self.assertNotIn("nearest Quick Ship size", reply)
        self.assertIn("Quoted size: 2 × 3 in.", item["description"])
        self.assertNotIn("nearest Quick Ship size", item["description"])
        unit = Decimal(str(item["unit_price"]))
        total = Decimal(str(item["total"]))
        self.assertEqual(unit * 5000, total)
        self.assertEqual(unit, Decimal("0.01678"))
        self.assertEqual(total, Decimal("83.90"))

    def test_reported_line_total_uses_full_unit_price(self) -> None:
        """5,000 × $0.0154 prints as $77.00; the catalog total is $77.20."""
        unit = unit_price_matching_total(Decimal("77.20"), 5000)
        self.assertEqual(unit, Decimal("0.01544"))
        self.assertEqual(unit * 5000, Decimal("77.20"))

        row = {**_NARROW, "_fortis_pricing_match": "closest_size"}
        reply, item = self._save(row, "Price for 2x3 white BOPP labels, quantity 5000")
        unit = Decimal(str(item["unit_price"]))
        total = Decimal(str(item["total"]))
        self.assertEqual(unit * Decimal(str(item["quantity"])), total)
        self.assertEqual(unit, Decimal("0.01544"))
        self.assertEqual(total, Decimal("77.20"))
        self.assertIn("$0.01544", reply)
        self.assertIn("$77.20", reply)
        self.assertNotIn("$0.0154 per", reply)


if __name__ == "__main__":
    unittest.main()
