"""Unit tests for structured estimate JSON helpers."""

from __future__ import annotations

import unittest

from fortis_cs_agent.estimate_json import parse_assistant_estimate_json


class TestParseAssistantEstimateJson(unittest.TestCase):
    def sample_payload(self) -> str:
        return (
            '{"business_name":"Acme","contact_name":"Brett Lloyd",'
            '"email":"a@b.co","phone":"","address":"123 St",'
            '"items":[{"sku":"SKU1","description":"2x3 WL","quantity":5000,'
            '"unit_price":0.01678,"total":83.9}],'
            '"notes":"Tax extra"}'
        )

    def test_raw_object(self) -> None:
        parsed = parse_assistant_estimate_json(self.sample_payload())
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["business_name"], "Acme")
        self.assertEqual(len(parsed["items"]), 1)
        self.assertEqual(parsed["items"][0]["sku"], "SKU1")

    def test_fenced_json(self) -> None:
        body = "```json\n" + self.sample_payload() + "\n```"
        parsed = parse_assistant_estimate_json(body)
        self.assertIsNotNone(parsed)

    def test_nested_prose_prefix(self) -> None:
        blob = "Certainly.\n" + self.sample_payload()
        parsed = parse_assistant_estimate_json(blob)
        self.assertIsNotNone(parsed)


if __name__ == "__main__":
    unittest.main()
