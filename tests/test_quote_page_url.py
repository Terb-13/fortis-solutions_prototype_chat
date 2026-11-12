"""Quote links stay on the portal when PUBLIC_BASE_URL is the chat host."""

import os
from unittest.mock import patch

from fortis_cs_agent.tools import PORTAL_QUOTE_ORIGIN, quote_page_url


def test_public_base_url_does_not_change_quote_host() -> None:
    chat = "https://fortis-solutions-prototype-chat.vercel.app"
    estimate_id = "b0539f03-85b1-4926-a944-6686a6c0e973"
    with patch.dict(os.environ, {"PUBLIC_BASE_URL": chat}):
        assert quote_page_url(estimate_id) == f"{PORTAL_QUOTE_ORIGIN}/quote/{estimate_id}"
