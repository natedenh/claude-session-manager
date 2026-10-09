from datetime import date

from csm import models, stats
from csm.data import Session

TODAY = date(2026, 10, 8)  # a Thursday


def test_names_and_prices():
    assert models.parse("claude-opus-5-5") == ("opus", "5.5") and models.label("us.anthropic.claude-sonnet-5-5") == "sonnet 5.5"
    assert models.parse("claude-haiku-4-5-20251001") == ("haiku", "4.5") and models.parse("claude-opus-5") == ("opus", "5")
    assert models.parse("claude-opus-5-5[1m]") == ("opus", "5.5") and models.parse("<synthetic>") is None
    assert models.family("claude-fable-5-1") == "fable" and models.label("weird") == "weird"
    assert models.rescale(10.0, "claude-opus-5-5", models.SONNET) == 5.0 and models.rescale(1.0, "gpt", models.SONNET) is None


def s(sid, **kw):
    return Session(id=sid, path="", title=sid, project="/p", cwd="/p", **kw)


def test_cost_by_model_splits_each_day_by_tokens_and_rescales():
    a = s("a", day_cost={"2026-10-07": 6.0}, day_models={"2026-10-07": {"claude-opus-5-5": 2.0, "claude-sonnet-5-5": 1.0}})
    b = s("b", day_cost={"2026-09-01": 9.0}, day_models={"2026-09-01": {"claude-opus-5": 1.0}})  # too old
    c = s("c", day_cost={"2026-10-06": 1.0})  # no model recorded
    got = stats.by_model([a, b, c], date(2026, 10, 5), TODAY, 0.0)
    assert got == {"claude-opus-5-5": 4.0, "claude-sonnet-5-5": 2.0, "": 1.0}
    from rich.console import Console
    con = Console(width=120, record=True, color_system=None)
    con.print(stats.Group(*stats.model_section([a, c], TODAY, 0.0)))
    out = con.export_text()
    # opus 4.0 -> 2.0 on Sonnet; sonnet 2.0 stays; unknown 1.0 stays: 5.0 of 7.0
    assert "opus 5.5" in out and "unknown" in out and "This week cost $7.00; on Sonnet 5.5 about $5.00 (-29%)" in out
