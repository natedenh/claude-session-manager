"""Model names as transcripts record them, and their relative prices.

Prices are Anthropic's list input prices per million tokens; output, cache writes and cache
reads keep the same ratios to input on every current model, so one number per model gives
the relative cost of the same work. They're only used as ratios, applied to what you actually
paid (your provider's prices may differ, but scale alike).
"""
from __future__ import annotations

import re

# family, version -> list input $ per million tokens
LIST_PRICE = {
    ("fable", "5.1"): 10.0, ("fable", "5"): 10.0,
    ("opus", "5.5"): 4.0, ("opus", "5"): 5.0, ("opus", "4.8"): 5.0, ("opus", "4.7"): 5.0, ("opus", "4.6"): 5.0,
    ("sonnet", "5.5"): 2.0, ("sonnet", "5"): 2.0, ("sonnet", "4.6"): 3.0,
    ("haiku", "5.5"): 0.10, ("haiku", "4.5"): 1.0,
}
SONNET, HAIKU = ("sonnet", "5.5"), ("haiku", "5.5")
NAME = re.compile(r"(fable|mythos|opus|sonnet|haiku)-(\d+)(?:-(\d))?(?=$|[-@:\[])")


def parse(model: str | None) -> tuple[str, str] | None:
    """("opus", "5.5") from claude-opus-5-5, us.anthropic.claude-opus-5-5, claude-haiku-4-5-20251001..."""
    if not model or (m := NAME.search(model)) is None:
        return None
    family = "fable" if m.group(1) == "mythos" else m.group(1)
    return family, m.group(2) + (f".{m.group(3)}" if m.group(3) else "")


def label(model: str | None) -> str:
    """"opus 5.5", or the raw name when it isn't one we know."""
    p = parse(model)
    return f"{p[0]} {p[1]}" if p else (model or "")


def family(model: str | None) -> str | None:
    p = parse(model)
    return p[0] if p else None


def price(model: str | None) -> float | None:
    p = parse(model)
    return LIST_PRICE.get(p) if p else None


def rescale(cost: float, model: str | None, to: tuple[str, str]) -> float | None:
    """What `cost` spent on `model` would have cost on `to`, at list-price ratios."""
    here = price(model)
    return cost * LIST_PRICE[to] / here if here else None
