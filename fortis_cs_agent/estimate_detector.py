"""
Heuristics for detecting shopper intent around quotes / estimates.
"""


def is_estimate_request(message: str) -> bool:
    """
    Detect if the user is asking for an estimate or quote.
    """
    keywords = [
        "estimate",
        "quote",
        "pricing",
        "cost",
        "how much",
        "create an estimate",
        "get a quote",
        "price for",
        "how much for",
        "can you quote",
    ]

    message_lower = message.lower()
    return any(keyword in message_lower for keyword in keywords)
