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

# Strong signals: user is actually asking for numbers / a quote (overrides refusals/capability heuristics).
_NO_DONT_BEFORE_VERB = r"(?<!\bdont\s)(?<!\bdon't\s)(?<!\bdo\snot\s)"

_STRONG_QUOTE_INTENT_RE = re.compile(
    r"(?i)"
    r"\b(send|give|request(ing)?|create|build|make| drawing\s+up|draw\s+up)\b.{0,60}\b"
    r"(estimate|quotes?|pricing|prices?|pricetag|line\s*items?|sku)\b"
    r"|"
    + _NO_DONT_BEFORE_VERB
    + r"\b(get|got|need|want)\b.{0,60}\b(estimate|quotes?|pricing|prices?|pricetag|line\s*items?|sku)\b"
    r"|\b(i['’]?d\s+like|looking\s+for|can\s+i\s+get|can\s+we\s+get|could\s+i\s+get)\b.{0,40}\b"
    r"(an?\s+)?(estimate|quotes?|pricing|prices?)\b"
    r"|\b(estimate|quote)\s+(for|on|with)\b"
    r"|\bfor\s+a\s+quote\b"
    r"|\bhow\s+much\s+(is|are|would|will|does|do)\b"
    r"|\bhow\s+much\s+for\b"
)

# User refuses a quote/estimate or steers to a non-pricing topic (checked before bare "estimate" keywords).
_ESTIMATE_REFUSAL_OR_OFF_TOPIC_RE = re.compile(
    r"(?i)"
    r"\bdon'?t\s+want\b.*\b(estimate|quotes?|pricing|prices?)\b"
    r"|\bdon'?t\s+need\b.*\b(estimate|quotes?|pricing|prices?)\b"
    r"|\bnot\s+interested\s+in\b.*\b(estimate|quotes?|pricing|prices?)\b"
    r"|\bnot\s+looking\s+for\b.*\b(estimate|quotes?|pricing|prices?)\b"
    r"|\bno\s+(thanks?,?\s+)?(estimate|quotes?)\b"
    r"|\babout\s+the\s+sbu\b"
    r"|\bknow\s+about\s+the\s+sbu\b"
    r"|\b(?:i['’]d|i\s+(?:would\s+)?like)\s+to\s+know\s+about\s+the\s+sbu\b"
)

# Informational "just asking…" without pricing words (handled after strict keyword pass).
_JUST_ASKING_ABOUT_RE = re.compile(r"(?i)\bjust\s+asking\s+about\b")

# Casual / meta questions about the agent — not a request to run the Quick Ship wizard.
# Match anywhere in the message (leading "Hi, " broke earlier ^-anchored patterns).
_CAPABILITY_OR_META_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bwhat\s+can\s+you\s+do\b"),
    re.compile(r"(?i)\bwhat\s+do\s+you\s+do\b"),
    re.compile(r"(?i)\bwhat\s+are\s+you\b"),
    re.compile(r"(?i)\bwhat\s+are\s+you\s+able\s+to\s+do\b"),
    re.compile(r"(?i)\bwhat\s+else\s+can\s+you(?:\s+help(?:\s+with)?)?\b"),
    re.compile(r"(?i)\bhow\s+can\s+you\s+help\b"),
    re.compile(r"(?i)\bwho\s+are\s+you\b"),
    re.compile(r"(?i)\bis\s+that\s+all\s+you\s+do\b"),
    re.compile(r"(?i)\bis\s+that\s+(all|it)\s*\?"),
    re.compile(r"(?i)\bis\s+that\s+everything\b"),
    re.compile(r"(?i)\bhow\s+does\s+this\s+work\b"),
    re.compile(r"(?i)\bwhat\s+is\s+this\b"),
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

# Phrase-level quote/pricing cues (word boundaries; avoids loose substrings like "cost" in unrelated words).
_STRICT_KEYWORD_RE = re.compile(
    r"(?i)"
    r"\bestimates?\b"
    r"|\bquotes?\b"
    r"|\bpricing\b"
    r"|\bprices?\b"
    r"|\ball-?in\b.{0,12}\bpric"
    r"|\bballpark\b"
    r"|\bhow\s+much\b"
    r"|\bget\s+a\s+(price|quote)\b"
    r"|\bget\s+quote\b"
    r"|\bget\s+pricing\b"
    r"|\bget\s+me\s+(a\s+)?(price|quote|estimate)\b"
    r"|\bcreate\s+(an?\s+)?estimate\b"
    r"|\bneed\s+(a\s+)?(quote|estimate)\b"
    r"|\bwant\s+(a\s+)?(quote|estimate)\b"
    r"|\bprice\s+quote\b"
    r"|\bpricing\s+out\b"
    r"|\bprice\s+for\b"
    r"|\bpricing\s+on\b"
    r"|\bcan\s+you\s+quote\b"
    r"|\bgive\s+me\s+(a\s+)?price\b"
    r"|\bwhat\s+would\s+it\s+cost\b"
    r"|\bquick\s+ship\b"
)

# Opening-turn hard block: never start the wizard on these (server + model guardrails).
# Substrings, not ^-anchored, so leading greetings still match.
_WIZARD_OPENER_HARD_BLOCK_RE = re.compile(
    r"(?i)"
    r"what\s+can\s+you\s+do"
    r"|what\s+do\s+you\s+do"
    r"|what\s+are\s+you\s+able\s+to\s+do"
    r"|how\s+can\s+you\s+help"
    r"|who\s+are\s+you"
    r"|i\s+don'?t\s+want\b.*\bestimate"
    r"|don'?t\s+want\b.*\bestimate"
    r"|don'?t\s+need\b.*\bestimate"
    r"|not\s+interested\s+in\b.*\bestimate"
    r"|'d\s+like\s+to\s+know\s+about\s+the\s+sbu"
    r"|would\s+like\s+to\s+know\s+about\s+the\s+sbu"
    r"|know\s+about\s+the\s+sbu"
    r"|about\s+the\s+sbu"
    r"|\bsbu\b.{0,80}\b(tell|tell\s+me|explain|information|learn|know\s+more)"
    r"|\bjust\s+asking\b"
)

# Like _STRICT_KEYWORD_RE but omits bare estimate/quote/prices tokens so refusals (“don’t want an estimate”)
# do not clear the opener hard block via a false-positive “buying” signal. Omits get/need/want+quote too (handled
# by _STRONG_QUOTE_INTENT_RE with don’t/do-not lookbehinds).
_STRICT_KEYWORD_RE_PHRASE_ONLY = re.compile(
    r"(?i)"
    r"\bpricing\b"
    r"|\ball-?in\b.{0,12}\bpric"
    r"|\bballpark\b"
    r"|\bhow\s+much\b"
    r"|\bcreate\s+(an?\s+)?estimate\b"
    r"|\bprice\s+quote\b"
    r"|\bpricing\s+out\b"
    r"|\bprice\s+for\b"
    r"|\bpricing\s+on\b"
    r"|\bcan\s+you\s+quote\b"
    r"|\bgive\s+me\s+(a\s+)?price\b"
    r"|\bwhat\s+would\s+it\s+cost\b"
    r"|\bquick\s+ship\b"
)


def _hard_block_quote_intent_override(text: str) -> bool:
    """True when phrasing should lift the opener hard block (but bare ‘estimate’ in a refusal does not)."""
    raw = (text or "").strip()
    if not raw:
        return False
    if _STRONG_QUOTE_INTENT_RE.search(raw):
        return True
    if _LABEL_QTY_RE.search(raw):
        return True
    low = raw.lower()
    if re.search(r"\b\d{2,7}\b", raw) and re.search(
        r"\b(labels?|stickers?|bopp|vinyl|poly|quick\s*ship|cmyk|flexo)\b",
        low,
    ):
        return True
    return bool(_STRICT_KEYWORD_RE_PHRASE_ONLY.search(low))


def _has_quote_buying_signals(text: str) -> bool:
    """True if the utterance includes explicit quote/pricing/product request cues (anywhere)."""
    raw = (text or "").strip()
    if not raw:
        return False
    if _STRONG_QUOTE_INTENT_RE.search(raw):
        return True
    if _LABEL_QTY_RE.search(raw):
        return True
    low = raw.lower()
    if re.search(r"\b\d{2,7}\b", raw) and re.search(
        r"\b(labels?|stickers?|bopp|vinyl|poly|quick\s*ship|cmyk|flexo)\b",
        low,
    ):
        return True
    return bool(_STRICT_KEYWORD_RE.search(low))


def _is_capability_or_meta_question(text: str) -> bool:
    """True for ‘who are you / what can you do’ style messages without quote-buying intent."""
    raw = (text or "").strip()
    if not raw or len(raw) > 220:
        return False
    if _has_quote_buying_signals(raw):
        return False
    for p in _CAPABILITY_OR_META_RES:
        if p.search(raw):
            return True
    return False


def is_estimate_request(message: str) -> bool:
    """
    Detect if the user is asking for an estimate or quote.

    Strict: requires clear pricing/quote intent, label qty, or product+quantity context.
    Excludes capability questions (even with a leading greeting) and explicit refusals.
    """
    text = shopper_utterance_for_estimate_heuristics(message)
    if not text:
        return False

    if _STRONG_QUOTE_INTENT_RE.search(text):
        return True
    if _LABEL_QTY_RE.search(text):
        return True

    message_lower = text.lower()
    if re.search(r"\b\d{2,7}\b", text) and re.search(
        r"\b(labels?|stickers?|bopp|vinyl|poly|quick\s*ship|cmyk|flexo)\b",
        message_lower,
    ):
        return True

    if _ESTIMATE_REFUSAL_OR_OFF_TOPIC_RE.search(text):
        return False

    if _is_capability_or_meta_question(text):
        return False

    if _ESTIMATE_NON_REQUEST_RE.search(text):
        return False

    if _STRICT_KEYWORD_RE.search(message_lower):
        return True

    if _JUST_ASKING_ABOUT_RE.search(text):
        return False

    return False


def shopper_utterance_for_estimate_heuristics(message: str) -> str:
    """
    Use the real shopper line when a proxy forwards an augmented blob.

    Model/RAG wrappers often end with ``Customer message:`` + the user’s words; scanning the
    whole blob falsely matches training words like “quote” / “pricing” and starts the wizard.
    """
    t = (message or "").strip()
    if not t:
        return ""
    t = re.sub(r"[\u200b-\u200d\ufeff]", "", t)
    t = t.replace("\u00a0", " ")
    parts = re.split(r"(?i)\bcustomer\s+message\s*:\s*", t)
    if len(parts) >= 2:
        core = parts[-1].strip()
        if core:
            return core
    return t.strip()


def should_skip_estimate_wizard_opener(message: str) -> bool:
    """
    If True, ``handle_estimate_flow`` must not *start* the wizard on this message.

    Quote/pricing/product signals override the hard block (e.g. “what can you do for a quote?”).
    """
    t = shopper_utterance_for_estimate_heuristics(message)
    if not t:
        return False
    if _hard_block_quote_intent_override(t):
        return False
    return bool(_WIZARD_OPENER_HARD_BLOCK_RE.search(t))
