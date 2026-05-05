"""
Heuristics for detecting shopper intent around quotes / estimates.
"""

from __future__ import annotations

import re

# Quantity + labels/stickers often appears without the word "quote" (e.g. "5000 labels 3x4 bopp").
_LABEL_QTY_RE = re.compile(
    r"\b(\d{1,7})\s*[-]?\s*(?:rolls?\s+of\s+)?(?:labels?|stickers?)\b",
    re.IGNORECASE,
)

# Rhetorical / capability questions — mention “estimate” but are not a quote request.
_ESTIMATE_NON_REQUEST_RE = re.compile(
    r"(?i)"
    r"(is\s+(that\s+)?(it|this|all)\s+)?(you\s+)?(can|could)\s+(you\s+)?(only|just)\s+.*\bestimate|"
    r"\b(is\s+)?all\s+you\s+can\s+do\b.*\bestimate|"
    r"\b(do|does)\s+you\s+only\b.*\bestimate|"
    r"\bnothing\s+(else|more)\b.*\bestimate|"
    r"\bnot\s+just\s+.*\bestimate|"
    r"\bbesides\b.*\bestimate\b.*\b(what|who|how|else)|"
    r"\bwhat\s+else\b.*\bestimate\b"
)


def is_estimate_request(message: str) -> bool:
    """
    Detect if the user is asking for an estimate or quote.

    Includes explicit quote/estimate language and common Quick Ship label order phrasing.
    """
    text = (message or "").strip()
    if not text:
        return False

    if _ESTIMATE_NON_REQUEST_RE.search(text):
        return False

    keywords = [
        "estimate",
        "quote",
        "pricing",
        "ballpark",
        "pricing out",
        "price quote",
        "cost",
        "how much",
        "create an estimate",
        "create estimate",
        "get a quote",
        "get quote",
        "need a quote",
        "want a quote",
        "place an order",
        "place order",
        "order labels",
        "label order",
        "buy labels",
        "need labels",
        "quick ship",
        "price for",
        "how much for",
        "can you quote",
        "give me a price",
        "what would it cost",
    ]

    message_lower = text.lower()
    if any(keyword in message_lower for keyword in keywords):
        return True

    if _LABEL_QTY_RE.search(text):
        return True

    # "I'd like 5000 ..." with label/sku context
    if re.search(r"\b\d{2,7}\b", text) and re.search(
        r"\b(labels?|stickers?|bopp|vinyl|poly|quick\s*ship|cmyk|flexo)\b",
        message_lower,
    ):
        return True

    return False
