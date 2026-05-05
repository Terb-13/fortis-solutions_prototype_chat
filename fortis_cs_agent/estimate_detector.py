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

# Strong signals: user is actually asking for numbers / a quote (overrides capability-only heuristics).
_STRONG_QUOTE_INTENT_RE = re.compile(
    r"(?i)"
    r"\b(get|got|need|want|send|give|request(ing)?|create|build|make| drawing\s+up|draw\s+up)\b.{0,60}\b"
    r"(estimate|quotes?|pricing|prices?|pricetag|line\s*items?|sku)\b"
    r"|\b(i['’]?d\s+like|looking\s+for|can\s+i\s+get|can\s+we\s+get|could\s+i\s+get)\b.{0,40}\b"
    r"(an?\s+)?(estimate|quotes?|pricing|prices?)\b"
    r"|\b(estimate|quote)\s+(for|on|with)\b"
    r"|\bhow\s+much\s+(is|are|would|will|does|do)\b"
    r"|\bhow\s+much\s+for\b"
)

# Casual / meta questions about the agent — not a request to run the Quick Ship wizard.
_CAPABILITY_OR_META_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)^\s*what\s+can\s+you\s+do\b"),
    re.compile(r"(?i)^\s*what\s+do\s+you\s+do\b"),
    re.compile(r"(?i)^\s*what\s+are\s+you\b"),
    re.compile(r"(?i)^\s*what\s+are\s+you\s+able\s+to\s+do\b"),
    re.compile(r"(?i)^\s*what\s+else\s+can\s+you\b"),
    re.compile(r"(?i)^\s*what\s+else\s+can\s+you\s+help\b"),
    re.compile(r"(?i)^\s*what\s+else\s+can\s+you\s+help\s+with\b"),
    re.compile(r"(?i)^\s*how\s+can\s+you\s+help\b"),
    re.compile(r"(?i)^\s*who\s+are\s+you\b"),
    re.compile(r"(?i)^\s*is\s+that\s+all\s+you\s+do\b"),
    re.compile(r"(?i)^\s*is\s+that\s+(all|it)\s*\??\s*$"),
    re.compile(r"(?i)^\s*is\s+that\s+everything\b"),
    re.compile(r"(?i)^\s*how\s+does\s+this\s+work\b"),
    re.compile(r"(?i)^\s*what\s+is\s+this\b"),
)

# Rhetorical questions that mention “estimate” / “only” but are not a quote request.
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


def _is_capability_or_meta_question(text: str) -> bool:
    """True for short ‘who are you / what can you do’ messages without quote-buying intent."""
    raw = (text or "").strip()
    if not raw or len(raw) > 160:
        return False
    if _STRONG_QUOTE_INTENT_RE.search(raw):
        return False
    if _LABEL_QTY_RE.search(raw):
        return False
    low = raw.lower()
    if re.search(r"\b\d{2,7}\b", raw) and re.search(
        r"\b(labels?|stickers?|bopp|vinyl|poly|quick\s*ship|cmyk|flexo)\b",
        low,
    ):
        return False
    for p in _CAPABILITY_OR_META_RES:
        m = p.search(raw)
        if not m:
            continue
        tail = raw[m.end() :].lower()
        # Same utterance asks for a quote / product — do not treat as meta-only.
        if re.search(
            r"\b(estimate|quotes?|pricing|prices?|labels?|stickers?|how\s+much|qty|quantity)\b|\d{3,}|"
            r"\bwith\s+(labels?|stickers?)\b|\bfor\s+(a|an|the|my)\s+(labels?|quote|estimate)\b",
            tail,
        ):
            continue
        return True
    return False


def is_estimate_request(message: str) -> bool:
    """
    Detect if the user is asking for an estimate or quote.

    Excludes generic capability questions (“what can you do?”). Requires clear buying/pricing
    intent or label quantity / product context.
    """
    text = (message or "").strip()
    if not text:
        return False

    if _is_capability_or_meta_question(text):
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
        "get pricing",
        "pricing on",
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
