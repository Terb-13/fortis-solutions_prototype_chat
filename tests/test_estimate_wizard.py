"""Quick Ship estimate wizard: detector + multi-step flow behavior."""

from __future__ import annotations

import unittest

from unittest.mock import patch

from fortis_cs_agent.estimate_detector import (
    is_estimate_request,
    should_exit_estimate_wizard_for_topic_shift,
    should_resume_estimate_wizard_from_paused,
    should_skip_estimate_wizard_opener,
)
from fortis_cs_agent.estimate_flow import handle_estimate_flow


class TestWrappedCustomerMessageBlob(unittest.TestCase):
    """Portal/proxy may POST augmented blobs; intent must use text after Customer message:."""

    def test_capability_after_wrapper_not_estimate(self) -> None:
        blob = (
            "Internal reference only. Quick Ship label quotes and pricing context.\n\n"
            "Customer message:\n"
            "what can you do?"
        )
        self.assertFalse(is_estimate_request(blob))
        self.assertTrue(should_skip_estimate_wizard_opener(blob))

    def test_real_quote_after_wrapper_still_intent(self) -> None:
        blob = "Training: estimates use SKUs.\n\nCustomer message:\ncreate an estimate"
        self.assertTrue(is_estimate_request(blob))

    def test_flow_skips_wizard_for_wrapped_capability(self) -> None:
        r = handle_estimate_flow(
            user_message=(
                "For Quick Ship use quote rows.\n\nCustomer message:\nwhat can you do?"
            ),
            conversation_history=[],
            conversation_id="00000000-0000-4000-8000-000000000040",
        )
        self.assertFalse(r.handled)

    def test_chatter_does_not_open_wizard_when_blob_has_quote_keywords(self) -> None:
        """Regression: training prefix must not force cold opener without explicit quote phrase."""
        r = handle_estimate_flow(
            user_message=(
                "We offer Quick Ship quotes and label pricing.\n\n"
                "Customer message:\nare you broken?"
            ),
            conversation_history=[],
            conversation_id="00000000-0000-4000-8000-000000000041",
        )
        self.assertFalse(r.handled)

    def test_specs_partial_step1_even_if_qty_dim_strict_fails(self) -> None:
        """Product-ish line should capture partial Step 1, not repeat full Got it opener."""
        r = handle_estimate_flow(
            user_message="5x6, 5000, white bopp, cmyk",
            conversation_history=[],
            conversation_id="00000000-0000-4000-8000-000000000042",
        )
        self.assertTrue(r.handled)
        self.assertNotIn("Got it — I’ll collect a structured Quick Ship estimate", r.reply)
        self.assertIn("Step 1/5", r.reply)
        self.assertIn("I still need", r.reply)
        self.assertNotIn("I’ve captured", r.reply)
        self.assertNotIn("5x6, 5000", r.reply)

    def test_user_message_marker_and_meta_chatter(self) -> None:
        blob = (
            "Quick Ship quotes and pricing context.\n\n"
            "User message:\n"
            "are you working now?"
        )
        self.assertFalse(is_estimate_request(blob))
        self.assertTrue(should_skip_estimate_wizard_opener(blob))
        r = handle_estimate_flow(
            user_message=blob,
            conversation_history=[],
            conversation_id="00000000-0000-4000-8000-000000000043",
        )
        self.assertFalse(r.handled)

    def test_still_not_alone_skips_wizard(self) -> None:
        self.assertTrue(should_skip_estimate_wizard_opener("still not"))
        self.assertFalse(
            handle_estimate_flow(
                user_message="still not",
                conversation_history=[],
                conversation_id="00000000-0000-4000-8000-000000000044",
            ).handled
        )


def _portal_thread_blob(last_user_line: str, *, assistant_has_specs: bool = False) -> str:
    """Mimic group-portal proxy: full markdown transcript + trailing instruction tail."""
    assistant = (
        "Hello! We do Quick Ship 2x3 white BOPP gloss CMYK 5000 labels."
        if assistant_has_specs
        else "Hello! I can help with packaging questions."
    )
    return (
        "Use the full thread below for context only. Respond to the most recent user message. "
        "If they ask something new or unrelated, answer that directly.\n\n"
        "--- Thread ---\n"
        "User: what can you do?\n\n"
        f"Assistant: {assistant}\n\n"
        f"User: {last_user_line}\n"
        "--- End thread ---\n\n"
        "Respond to the most recent user message using the thread above."
    )


class TestFullThreadProxyBlob(unittest.TestCase):
    """Regression: proxy sends --- Thread --- wrappers; wizard must use last User: line only."""

    def test_create_estimate_blob_is_cold_opener_not_polluted_specs(self) -> None:
        blob = _portal_thread_blob("can you create an estimate?", assistant_has_specs=True)
        r = handle_estimate_flow(
            user_message=blob,
            conversation_history=[],
            conversation_id="00000000-0000-4000-8000-000000000060",
        )
        self.assertTrue(r.handled)
        self.assertNotIn("I’ve captured", r.reply)
        self.assertNotIn("Use the full thread", r.reply)
        self.assertIn("Step 1/5", r.reply)
        assert r.assistant_meta is not None
        self.assertEqual(r.assistant_meta["estimate_flow"]["pending_step_index"], 0)
        self.assertFalse((r.assistant_meta["estimate_flow"]["draft"] or {}).get("product_details"))

    def test_specs_only_last_user_line_advances_or_clean_partial(self) -> None:
        blob = _portal_thread_blob("2x3 white bopp, 5000, gloss, cmyk", assistant_has_specs=True)
        r = handle_estimate_flow(
            user_message=blob,
            conversation_history=[],
            conversation_id="00000000-0000-4000-8000-000000000061",
        )
        self.assertTrue(r.handled)
        assert r.assistant_meta is not None
        pd = str((r.assistant_meta["estimate_flow"]["draft"] or {}).get("product_details") or "")
        self.assertIn("2x3", pd)
        self.assertIn("5000", pd)
        self.assertNotIn("Hello!", pd)
        self.assertNotIn("Use the full thread", pd)
        self.assertEqual(r.assistant_meta["estimate_flow"]["pending_step_index"], 1)
        self.assertIn("Step 2/5", r.reply)

    def test_step2_thread_wrap_advances(self) -> None:
        hist = [
            {
                "role": "assistant",
                "content": "**Step 2/5:** …",
                "meta": {
                    "estimate_flow": {
                        "active": True,
                        "version": 1,
                        "pending_step_index": 1,
                        "draft": {
                            "product_details": "5000 2x3 white bopp gloss cmyk",
                            "_quantity_hint": 5000,
                        },
                    }
                },
            }
        ]
        blob = _portal_thread_blob("lloydco", assistant_has_specs=False)
        r = handle_estimate_flow(
            user_message=blob,
            conversation_history=hist,
            conversation_id="00000000-0000-4000-8000-000000000062",
        )
        self.assertTrue(r.handled)
        assert r.assistant_meta is not None
        self.assertEqual(r.assistant_meta["estimate_flow"]["pending_step_index"], 2)
        self.assertEqual(r.assistant_meta["estimate_flow"]["draft"].get("business_name"), "lloydco")
        self.assertIn("Step 3/5", r.reply)


class TestWizardTopicShift(unittest.TestCase):
    """Mid–quote-flow SBU / capability questions should pause the session, not advance the wizard."""

    @patch("fortis_cs_agent.estimate_flow.update_estimate_session_status")
    @patch("fortis_cs_agent.estimate_flow.fetch_estimate_session")
    def test_sbu_pivot_pauses_in_progress_session(self, mock_fetch, mock_update) -> None:
        mock_fetch.return_value = {
            "status": "in_progress",
            "collected_data": {
                "product_details": "5000 2x3 white bopp gloss cmyk",
                "quantity": 5000,
                "business_name": None,
                "contact_name": None,
                "email": None,
                "address": None,
            },
        }
        r = handle_estimate_flow(
            user_message="This looks good. Can you tell me about the SBU?",
            conversation_history=[],
            conversation_id="00000000-0000-4000-8000-000000000070",
        )
        self.assertFalse(r.handled)
        mock_update.assert_called_with("00000000-0000-4000-8000-000000000070", "paused")

    @patch("fortis_cs_agent.estimate_flow.update_estimate_session_status")
    @patch("fortis_cs_agent.estimate_flow.fetch_estimate_session")
    def test_why_arent_you_working_pauses(self, mock_fetch, mock_update) -> None:
        mock_fetch.return_value = {
            "status": "in_progress",
            "collected_data": {"product_details": "5000 2x3 white bopp gloss cmyk", "quantity": 5000},
        }
        r = handle_estimate_flow(
            user_message="Why aren't you working?!",
            conversation_history=[],
            conversation_id="00000000-0000-4000-8000-000000000071",
        )
        self.assertFalse(r.handled)
        mock_update.assert_called()

    def test_topic_shift_heuristic_detects_sbu_and_not_resume_on_company_name(self) -> None:
        self.assertTrue(should_exit_estimate_wizard_for_topic_shift("tell me about the sbu"))
        self.assertTrue(should_exit_estimate_wizard_for_topic_shift("what can you do?"))
        self.assertFalse(should_resume_estimate_wizard_from_paused("lloydco"))
        self.assertTrue(should_resume_estimate_wizard_from_paused("I need a quote for labels"))


class TestCollectedDataMapping(unittest.TestCase):
    """JSONB ``collected_data`` ↔ internal draft helpers (no Supabase required)."""

    def test_round_trip_preserves_product_and_contact_fields(self) -> None:
        from fortis_cs_agent.estimate_flow import _draft_to_stored_collected, _stored_collected_to_draft

        draft = {
            "product_details": "5000 2x3 white bopp gloss cmyk",
            "_quantity_hint": 5000,
            "business_name": "Acme LLC",
            "contact_name": "Jane Doe",
        }
        flat = _draft_to_stored_collected(draft)
        self.assertEqual(flat.get("quantity"), 5000)
        self.assertEqual(flat.get("business_name"), "Acme LLC")
        back = _stored_collected_to_draft(flat)
        self.assertIn("2x3", back.get("product_details", ""))
        self.assertEqual(back.get("business_name"), "Acme LLC")
        self.assertEqual(back.get("contact_name"), "Jane Doe")


class TestShouldSkipWizardOpener(unittest.TestCase):
    def test_blocks_meta_and_refusals(self) -> None:
        for msg in (
            "what can you do?",
            "Hi — what can you do?",
            "I don't want an estimate.",
            "I'd like to know about the sbu",
            "just asking about your return policy",
        ):
            self.assertTrue(should_skip_estimate_wizard_opener(msg), msg=repr(msg))

    def test_overridden_by_quote_intent(self) -> None:
        self.assertFalse(should_skip_estimate_wizard_opener("what can you do for a quote?"))
        self.assertFalse(should_skip_estimate_wizard_opener("just asking about pricing for 2x3 labels"))


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

    def test_rhetorical_estimate_mention_not_intent(self) -> None:
        self.assertFalse(is_estimate_request("is all you can do is estimate?"))
        self.assertFalse(is_estimate_request("do you only do estimates?"))

    def test_capability_questions_not_estimate_intent(self) -> None:
        self.assertFalse(is_estimate_request("what can you do?"))
        self.assertFalse(is_estimate_request("What can you do"))
        self.assertFalse(is_estimate_request("is that all you do?"))
        self.assertFalse(is_estimate_request("what else can you help with?"))
        self.assertFalse(is_estimate_request("how can you help?"))
        self.assertFalse(is_estimate_request("Hi, what can you do?"))
        self.assertFalse(is_estimate_request("Hey — what can you do?"))

    def test_explicit_refusal_and_sbu_not_estimate_intent(self) -> None:
        self.assertFalse(is_estimate_request("I don't want an estimate."))
        self.assertFalse(
            is_estimate_request("I don't want an estimate. I'd like to know about the sbu")
        )
        self.assertFalse(is_estimate_request("just asking about your company"))
        self.assertTrue(is_estimate_request("just asking about pricing for 2x3 labels"))

    def test_quote_phrases_after_capability_still_intent(self) -> None:
        self.assertTrue(is_estimate_request("what can you do for a quote?"))
        self.assertTrue(is_estimate_request("what can you do to get me pricing?"))


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
        self.assertIn("I still need", r.reply)
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

    def test_recovery_after_grok_and_labeled_message(self) -> None:
        """Rebuild draft from Step 1 + prior user WxH even when a Grok reply broke meta continuity."""
        history = [
            {
                "role": "assistant",
                "content": "**Step 1/5:** In one line send **quantity**, **size …**",
                "meta": None,
            },
            {"role": "user", "content": "5x6"},
            {
                "role": "assistant",
                "content": "It looks like you're providing dimensions—perhaps for a label quote?",
                "meta": None,
            },
        ]
        r = handle_estimate_flow(
            user_message=(
                "quantity: 500, material: white bopp, finish: gloss, print colors: cmyk, "
                "business name: test_123"
            ),
            conversation_history=history,
            conversation_id="00000000-0000-4000-8000-000000000020",
        )
        self.assertTrue(r.handled)
        self.assertIn("Step 3/5", r.reply)
        assert r.assistant_meta is not None
        d = r.assistant_meta["estimate_flow"]["draft"]
        self.assertEqual(d.get("business_name"), "test_123")
        self.assertIn("5x6", d.get("product_details", ""))

    def test_non_estimate_phrases_skip_wizard(self) -> None:
        for msg in (
            "what can you do?",
            "Hi, what can you do?",
            "I don't want an estimate. I'd like to know about the sbu",
        ):
            r = handle_estimate_flow(
                user_message=msg,
                conversation_history=[],
                conversation_id="00000000-0000-4000-8000-000000000030",
            )
            self.assertFalse(r.handled, msg=repr(msg))


class TestWizardStepValidation(unittest.TestCase):
    """Invalid short answers should acknowledge, re-ask differently, and stay on the same step."""

    def _history_at_step(self, pending: int, draft: dict) -> list[dict]:
        return [
            {
                "role": "assistant",
                "content": f"**Step {pending + 1}/5:** …",
                "meta": {
                    "estimate_flow": {
                        "active": True,
                        "version": 1,
                        "pending_step_index": pending,
                        "draft": dict(draft),
                    }
                },
            }
        ]

    def test_step2_gibberish_stays_with_varied_reply(self) -> None:
        hist = self._history_at_step(
            1,
            {"product_details": "1000 2x3 bopp gloss cmyk", "_quantity_hint": 1000},
        )
        r = handle_estimate_flow(
            user_message="wtf",
            conversation_history=hist,
            conversation_id="00000000-0000-4000-8000-000000000050",
        )
        self.assertTrue(r.handled)
        assert r.assistant_meta is not None
        self.assertEqual(r.assistant_meta["estimate_flow"]["pending_step_index"], 1)
        self.assertIsNone(r.assistant_meta["estimate_flow"]["draft"].get("business_name"))
        self.assertIn("(again)", r.reply)
        self.assertIn("**wtf**", r.reply)
        self.assertNotIn("What **business name** should appear on the quote?", r.reply)

    def test_step2_too_short_then_valid_advances(self) -> None:
        hist = self._history_at_step(
            1,
            {"product_details": "1000 2x3 bopp gloss cmyk", "_quantity_hint": 1000},
        )
        r_short = handle_estimate_flow(
            user_message="Co",
            conversation_history=hist,
            conversation_id="00000000-0000-4000-8000-000000000051",
        )
        self.assertTrue(r_short.handled)
        assert r_short.assistant_meta is not None
        self.assertEqual(r_short.assistant_meta["estimate_flow"]["pending_step_index"], 1)

        r_ok = handle_estimate_flow(
            user_message="lloydco",
            conversation_history=hist,
            conversation_id="00000000-0000-4000-8000-000000000052",
        )
        self.assertTrue(r_ok.handled)
        assert r_ok.assistant_meta is not None
        self.assertEqual(r_ok.assistant_meta["estimate_flow"]["pending_step_index"], 2)
        self.assertEqual(r_ok.assistant_meta["estimate_flow"]["draft"].get("business_name"), "lloydco")

    def test_step3_contact_invalid_stays(self) -> None:
        hist = self._history_at_step(
            2,
            {
                "product_details": "1000 2x3 bopp gloss cmyk",
                "_quantity_hint": 1000,
                "business_name": "Acme",
            },
        )
        r = handle_estimate_flow(
            user_message="lol",
            conversation_history=hist,
            conversation_id="00000000-0000-4000-8000-000000000053",
        )
        self.assertTrue(r.handled)
        assert r.assistant_meta is not None
        self.assertEqual(r.assistant_meta["estimate_flow"]["pending_step_index"], 2)
        self.assertIsNone(r.assistant_meta["estimate_flow"]["draft"].get("contact_name"))
        self.assertIn("(again)", r.reply)

    def test_step4_bad_email_stays(self) -> None:
        hist = self._history_at_step(
            3,
            {
                "product_details": "1000 2x3 bopp gloss cmyk",
                "_quantity_hint": 1000,
                "business_name": "Acme",
                "contact_name": "Sam Jones",
            },
        )
        r = handle_estimate_flow(
            user_message="not-an-email",
            conversation_history=hist,
            conversation_id="00000000-0000-4000-8000-000000000054",
        )
        self.assertTrue(r.handled)
        assert r.assistant_meta is not None
        self.assertEqual(r.assistant_meta["estimate_flow"]["pending_step_index"], 3)
        self.assertIsNone(r.assistant_meta["estimate_flow"]["draft"].get("email"))
        self.assertIn("not-an-email", r.reply)
        self.assertIn("(again)", r.reply)


if __name__ == "__main__":
    unittest.main()
