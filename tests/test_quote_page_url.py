"""Quote links must target the portal, even when PUBLIC_BASE_URL is the chat host."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fortis_cs_agent.tools import PORTAL_QUOTE_ORIGIN, quote_page_url

_CHAT_HOST = "https://fortis-solutions-prototype-chat.vercel.app"
_ESTIMATE_ID = "b0539f03-85b1-4926-a944-6686a6c0e973"


class TestQuotePageUrl(unittest.TestCase):
    def test_chat_host_public_base_url_still_uses_portal(self) -> None:
        env = {
            "PUBLIC_BASE_URL": _CHAT_HOST,
            "NEXT_PUBLIC_QUOTE_SITE_URL": "",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual(
                quote_page_url(_ESTIMATE_ID),
                f"{PORTAL_QUOTE_ORIGIN}/quote/{_ESTIMATE_ID}",
            )

    def test_unset_env_uses_portal(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                quote_page_url(_ESTIMATE_ID),
                f"{PORTAL_QUOTE_ORIGIN}/quote/{_ESTIMATE_ID}",
            )

    def test_quote_site_override_wins_and_strips_slash(self) -> None:
        env = {
            "PUBLIC_BASE_URL": _CHAT_HOST,
            "NEXT_PUBLIC_QUOTE_SITE_URL": "http://localhost:3000/",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual(
                quote_page_url(_ESTIMATE_ID),
                f"http://localhost:3000/quote/{_ESTIMATE_ID}",
            )


if __name__ == "__main__":
    unittest.main()
