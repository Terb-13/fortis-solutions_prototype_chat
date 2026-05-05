"""Quick Ship estimate wizard: detector + multi-step flow behavior."""

from __future__ import annotations

import unittest

from fortis_cs_agent.estimate_detector import is_estimate_request
from fortis_cs_agent.estimate_flow import handle_estimate_flow


class TestEstimateDetector(unittest.TestCase):
    def test_explicit_estimate_phrases(self) -> None:
        self.assertTrue(is_estimate_request("can you create me an estimate?"))
        self.assertTrue(is_estimate_request("create an estimate"))
        self.assertTrue(is_estimate_request("get a quote for labels"))

    def test_qty_labels_without_quote_word(self) -> None:
        self.assertTrue(is_estimate_request("5000 labels"))
        self.assertTrue(is_estimate_request("need 2500 stickers 2x3"))

    def test_not_estimate_small_talk(self) -> None:
        self.assertFalse(is_estimate_request("hello"))
        self.assertFalse(is_estimate_request("what are your hours?"))


class TestEstimateWizardFlow(unittest.TestCase):
    def test_opener_starts_step_1(self) -> None:
        r = handle_estimate_flow(
            user_message="can you create me an estimate?",
            conversation_history=[],
            conversation_id="00000000-0000-4000-8000-000000000001",
        )
        self.assertTrue(r.handled)
        self.assertIn("Step 1/5", r.reply)
        assert r.assistant_meta is not None
        ef = r.assistant_meta["estimate_flow"]
        self.assertTrue(ef["active"])
        self.assertEqual(ef["pending_step_index"], 0)

    def test_company_name_with_quote_does_not_reset_wizard(self) -> None:
        history = [
            {
                "role": "assistant",
                "content": "…",
                "meta": {
                    "estimate_flow": {
                        "active": True,
                        "version": 1,
                        "pending_step_index": 1,
                        "draft": {"product_details": "1000 2x3 bopp gloss cmyk", "_quantity_hint": 1000},
                    }
                },
            }
        ]
        r = handle_estimate_flow(
            user_message="Acme Quote Co",
            conversation_history=history,
            conversation_id="00000000-0000-4000-8000-000000000002",
        )
        self.assertTrue(r.handled)
        self.assertIn("Step 3/5", r.reply)
        assert r.assistant_meta is not None
        self.assertEqual(r.assistant_meta["estimate_flow"]["pending_step_index"], 2)
        self.assertEqual(
            r.assistant_meta["estimate_flow"]["draft"].get("business_name"),
            "Acme Quote Co",
        )

    def test_restart_phrase_resets_to_step_1(self) -> None:
        history = [
            {
                "role": "assistant",
                "content": "…",
                "meta": {
                    "estimate_flow": {
                        "active": True,
                        "version": 1,
                        "pending_step_index": 2,
                        "draft": {"product_details": "x", "business_name": "y"},
                    }
                },
            }
        ]
        r = handle_estimate_flow(
            user_message="start over with a new quote",
            conversation_history=history,
            conversation_id="00000000-0000-4000-8000-000000000003",
        )
        self.assertTrue(r.handled)
        self.assertIn("Step 1/5", r.reply)
        assert r.assistant_meta is not None
        self.assertEqual(r.assistant_meta["estimate_flow"]["pending_step_index"], 0)
        self.assertEqual(r.assistant_meta["estimate_flow"]["draft"], {})

    def test_partial_dimensions_stay_on_step_1(self) -> None:
        history = [
            {
                "role": "assistant",
                "content": "**Step 1/5:** In one line send quantity…",
                "meta": {
                    "estimate_flow": {
                        "active": True,
                        "version": 1,
                        "pending_step_index": 0,
                        "draft": {},
                    }
                },
            }
        ]
        r = handle_estimate_flow(
            user_message="2x3",
            conversation_history=history,
            conversation_id="00000000-0000-4000-8000-000000000010",
        )
        self.assertTrue(r.handled)
        self.assertIn("Still need", r.reply)
        assert r.assistant_meta is not None
        self.assertEqual(r.assistant_meta["estimate_flow"]["pending_step_index"], 0)
        self.assertIn("2x3", r.assistant_meta["estimate_flow"]["draft"]["product_details"])

    def test_merge_second_line_advances_to_step_2(self) -> None:
        history = [
            {
                "role": "assistant",
                "content": "…",
                "meta": {
                    "estimate_flow": {
                        "active": True,
                        "version": 1,
                        "pending_step_index": 0,
                        "draft": {"product_details": "2x3"},
                    }
                },
            }
        ]
        r = handle_estimate_flow(
            user_message="2500, white bopp, gloss, cmyk",
            conversation_history=history,
            conversation_id="00000000-0000-4000-8000-000000000011",
        )
        self.assertTrue(r.handled)
        self.assertIn("Step 2/5", r.reply)
        assert r.assistant_meta is not None
        blob = r.assistant_meta["estimate_flow"]["draft"]["product_details"]
        self.assertIn("2x3", blob)
        self.assertIn("2500", blob)

    def test_snapshot_coerces_meta_json_string(self) -> None:
        import json

        from fortis_cs_agent.estimate_flow import latest_estimate_flow_snapshot

        meta = json.dumps(
            {
                "estimate_flow": {
                    "active": True,
                    "version": 1,
                    "pending_step_index": 0,
                    "draft": {},
                }
            }
        )
        hist = [{"role": "assistant", "content": "x", "meta": meta}]
        snap = latest_estimate_flow_snapshot(hist)
        self.assertIsNotNone(snap)
        assert snap is not None
        self.assertEqual(snap[1], 0)

    def test_snapshot_infers_step_from_assistant_copy(self) -> None:
        from fortis_cs_agent.estimate_flow import latest_estimate_flow_snapshot

        hist = [
            {
                "role": "assistant",
                "content": "**Step 1/5:** In one line send **quantity**, **size (W×H in.)**, **material**…",
                "meta": None,
            }
        ]
        snap = latest_estimate_flow_snapshot(hist)
        self.assertIsNotNone(snap)
        assert snap is not None
        self.assertEqual(snap[1], 0)
        self.assertEqual(snap[0], {})


if __name__ == "__main__":
    unittest.main()
