"""Guard against duplicate estimate_flow symbol imports in api.py (merge-regression)."""

from __future__ import annotations

import unittest
from pathlib import Path


class TestApiEstimateFlowImport(unittest.TestCase):
    def test_single_module_alias_only(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        text = (repo / "fortis_cs_agent" / "api.py").read_text(encoding="utf-8")
        legacy = 'from fortis_cs_agent.estimate_flow import'
        self.assertNotIn(
            legacy,
            text,
            f"Remove `{legacy} ...` and use only "
            "`from fortis_cs_agent import estimate_flow as _estimate_flow`.",
        )
        canonical = "from fortis_cs_agent import estimate_flow as _estimate_flow"
        self.assertEqual(
            text.count(canonical),
            1,
            f"Expected exactly one line: {canonical!r}",
        )


if __name__ == "__main__":
    unittest.main()
