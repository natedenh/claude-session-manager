from datetime import datetime

from conftest import rec, user

from csm import costs
from csm.app import CSM
from csm.data import Session
from test_app import settle

NOW = datetime(2026, 10, 7, 12).timestamp()  # a Wednesday


def ts(y, m, d, h=12):
    return datetime(y, m, d, h).timestamp()


def sess(id, cost, mtime, project="/p/a"):
    return Session(id=id, path="", title=id, project=project, cwd=project, cost=cost, mtime=mtime)


def test_week_boundary_sunday_vs_monday():
    ss = [sess("sun", 1.0, datetime(2026, 10, 4, 23, 59).timestamp()),
          sess("mon", 2.0, datetime(2026, 10, 5, 0, 0).timestamp())]
    weeks = dict(costs.by_week(ss, NOW))
    assert weeks[datetime(2026, 9, 28).date()] == 1.0
    assert weeks[datetime(2026, 10, 5).date()] == 2.0
    assert len(weeks) == 12


def test_weeks_drop_old_and_keep_empty():
    rows = costs.by_week([sess("old", 5.0, ts(2025, 1, 1))], NOW)
    assert [c for _, c in rows] == [0.0] * 12
    assert rows[0][0] == datetime(2026, 10, 5).date()


def test_project_totals_sorted_and_none_ignored():
    ss = [sess("a", 1.0, NOW), sess("b", 2.0, NOW), sess("c", None, NOW),
          sess("d", 10.0, NOW, "/p/big")]
    assert costs.by_project(ss) == [("big", 1, 10.0), ("a", 2, 3.0)]


def test_totals_windows():
    day = 86400
    ss = [sess("new", 1.0, NOW - day), sess("mid", 2.0, NOW - 20 * day),
          sess("old", 4.0, NOW - 60 * day), sess("none", None, NOW)]
    t = costs.totals(ss, NOW)
    assert (t.all_time, t.week, t.month, t.count) == (7.0, 1.0, 3.0, 3)


def test_top_sessions_limit():
    ss = [sess(str(i), float(i), NOW) for i in range(20)] + [sess("n", None, NOW)]
    top = costs.top_sessions(ss)
    assert len(top) == 10 and top[0].cost == 19.0


async def test_dollar_opens_and_esc_closes(paths, write, tmp_path):
    p = str(tmp_path / "alpha")
    write(p, "a1", user("hi", p), rec(type="cost-state", totalCostUSD=1.5))
    app = CSM(paths)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("dollar_sign")
        assert isinstance(app.screen, costs.Costs)
        await pilot.press("escape")
        assert not isinstance(app.screen, costs.Costs)
        await pilot.press("dollar_sign", "dollar_sign")
        assert not isinstance(app.screen, costs.Costs)
