from datetime import date, timedelta

from rich.console import Console

from csm import stats
from csm.data import Session
from test_app import sessions, settle  # noqa: F401  (fixture)

TODAY = date(2026, 10, 7)  # a Wednesday


def s(sid, project="/p/alpha", **kw):
    return Session(id=sid, path="", title=sid, project=project, cwd=project, **kw)


def d(n):
    return (TODAY - timedelta(days=n)).isoformat()


def test_streaks_allow_today_to_be_quiet_so_far():
    days = [TODAY - timedelta(days=i) for i in (1, 2, 3, 6, 7)]
    assert stats.streaks(days, TODAY) == (3, 3)
    assert stats.streaks([*days, TODAY], TODAY) == (4, 4)
    assert stats.streaks([TODAY - timedelta(days=2)], TODAY) == (0, 1)


def test_bar_uses_eighths():
    assert stats.bar(0, 10) == "" and stats.bar(10, 10, 4) == "████"
    assert stats.bar(5, 10, 3) == "█▌" and stats.bar(0.001, 10, 4) == "▏"


def test_daily_and_totals_add_up_across_sessions():
    ss = [s("a", active={d(0): 30, d(8): 10}, day_cost={d(0): 1.5}, day_lines={d(0): 7}),
          s("b", active={d(0): 15}, day_cost={d(1): 2.0})]
    days = stats.daily(ss)
    assert days[TODAY].minutes == 45 and days[TODAY].sessions == {"a", "b"} and days[TODAY].lines == 7
    week = stats.totals(days, TODAY - timedelta(days=2), TODAY)
    assert week.minutes == 45 and week.cost == 3.5


def plain(group):
    c = Console(width=100, record=True, color_system=None)
    c.print(group)
    return c.export_text()


def test_render_shows_the_sections():
    hours = [0] * 24
    hours[14] = 40
    ss = [s("a", active={d(0): 90, d(1): 30}, day_cost={d(0): 4.0}, hours=hours, skills={"browser-test": 3}),
          s("b", project="/p/beta", active={d(2): 20})]
    out = plain(stats.render(ss, TODAY))
    assert "1h 30m" in out and "Streak 3 days" in out and "browser-test" in out and "3×" in out
    assert "Wed Oct 7" in out and out.index("alpha") < out.index("beta")  # busiest project first
    assert "Sep 28" not in out.split("Cost by week")[1].split("When")[0]  # nothing recorded that far back
    assert stats.render([], TODAY).renderables[0].plain.startswith("No history yet")


async def test_I_opens_stats(sessions):  # noqa: F811
    from csm.app import CSM
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("I")
        assert isinstance(app.screen, stats.Stats)
        await pilot.press("escape")
        assert not isinstance(app.screen, stats.Stats)
