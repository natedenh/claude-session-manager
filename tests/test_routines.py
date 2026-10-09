import json
import time
from datetime import datetime

import pytest

from csm import data, routines
from csm.routines_view import RoutineView, Routines, gist
from test_app import CSM, SessionList, sessions, settle  # noqa: F401  (fixture)

NOW = datetime(2026, 10, 9, 10, 0)  # a Friday


def test_cron_next_fire_and_words():
    assert routines.next_fire("0 */6 * * *", datetime(2026, 10, 8, 21, 40)) == datetime(2026, 10, 9, 0, 0)
    assert routines.next_fire("30 9 * * 1-5", NOW) == datetime(2026, 10, 12, 9, 30)  # skips the weekend
    assert routines.next_fire("0 9 * * 7", NOW) == datetime(2026, 10, 11, 9, 0)  # 7 is Sunday
    assert routines.next_fire("0 0 1 * *", NOW) == datetime(2026, 11, 1, 0, 0)
    assert routines.next_fire("nonsense", NOW) is None
    assert routines.describe("0 */6 * * *") == "every 6 hours" and routines.describe("15 * * * *") == "every hour"
    assert routines.describe("30 9 * * 1-5") == "weekdays at 9:30 AM" and routines.describe("0 18 * * *") == "daily at 6:00 PM"
    assert routines.describe("0 9 * * 1") == "Mondays at 9:00 AM" and routines.describe("*/15 * * * *") == "every 15 minutes"
    assert routines.describe("0 9 1 * *") == "cron 0 9 1 * *"


def test_gist_keeps_the_first_sentence():
    assert gist("Not available yet. Only global is listed.") == "Not available yet."
    assert gist("Version 5.5 is out") == "Version 5.5 is out" and len(gist("x" * 300)) == 120


def write_registry(paths, tasks, runs=()):
    org = paths.desktop / "Claude-3p" / "claude-code-sessions" / "acct" / "org"
    org.mkdir(parents=True, exist_ok=True)
    (org / "scheduled-tasks.json").write_text(json.dumps({"scheduledTasks": tasks, "recordedSkips": {}}))
    for i, (task, cli, result, created) in enumerate(runs):
        (org / f"local_{i}.json").write_text(json.dumps({
            "sessionId": f"local_{i}", "cliSessionId": cli, "scheduledTaskId": task, "createdAt": created * 1000,
            "postTurnSummary": {"status_category": "review_ready", "status_detail": result} if result else None}))


@pytest.fixture
def with_routines(sessions, tmp_path):  # noqa: F811
    skill = tmp_path / "skills" / "watch" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: watch\ndescription: Every 6h, check the thing.\n---\n\nCheck **the thing**.\n")
    write_registry(sessions, [
        {"id": "watch", "displayName": "Watch the thing", "cronExpression": "0 */6 * * *", "enabled": True,
         "filePath": str(skill), "cwd": "/w", "createdAt": 1790000000000, "lastRunAt": "2026-10-01T13:46:49.679Z"},
        {"id": "once", "displayName": "Remind me once", "fireAt": 1791378000000, "enabled": False,
         "filePath": "/missing/SKILL.md", "lastRunAt": "2026-10-09T01:34:19.353Z"},
    ], runs=[("watch", "a1", "Not there yet. Still waiting.", 1_000_100), ("watch", "a2", None, 1_000_200)])
    return sessions


def test_load_reads_the_registry_and_skills(with_routines):
    rs = {r.id: r for r in routines.load(with_routines)}
    w, o = rs["watch"], rs["once"]
    assert (w.status, w.schedule, w.description, w.instructions) == ("active", "every 6 hours",
                                                                     "Every 6h, check the thing.", "Check **the thing**.")
    assert w.next_run(datetime(2026, 10, 8, 21, 40)) == datetime(2026, 10, 9, 0, 0)
    assert o.status == "completed" and o.schedule.startswith("once, ") and o.next_run() is None
    recs = data.load_desktop(with_routines)
    assert recs["a1"].routine == "watch" and recs["a1"].result == "Not there yet. Still waiting." and recs["a2"].result is None


async def test_routines_row_screens_and_runs(with_routines):
    app = CSM(with_routines)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot)
        lst = app.query_one(SessionList)
        row = next(o for o in lst.options if o.id == "R:routines")
        assert "Routines  1 active · next in" in row.prompt.plain
        assert app.row(app.by_id["a1"]).plain.startswith("  ◷ ")  # a run is marked
        assert "a run of the routine “Watch the thing”: Not there yet." in "".join(
            str(r) for r in app.meta(app.by_id["a1"]).renderables)
        await pilot.press("U")
        await settle(pilot)
        assert isinstance(app.screen, Routines)
        await pilot.press("enter")  # the first routine
        await settle(pilot)
        assert isinstance(app.screen, RoutineView) and len(app.screen.routine.runs) == 2
        assert app.screen.query_one("#runs").get_option_at_index(0).id == "a2"  # newest first
        await pilot.press("down", "enter")  # a1's run
        await settle(pilot)
        assert not isinstance(app.screen, (Routines, RoutineView))
        assert lst.highlighted_option.id == "s:a1"


async def test_a_new_result_is_announced_once(with_routines, sent):
    app = CSM(with_routines)
    async with app.run_test() as pilot:
        await settle(pilot)
        assert sent == []  # what was there at start isn't news
        write_registry(with_routines, [{"id": "watch", "displayName": "Watch the thing", "cronExpression": "0 */6 * * *",
                                        "enabled": True, "filePath": "/x"}],
                       runs=[("watch", "a1", "Not there yet. Still waiting.", 1_000_100),
                             ("watch", "a2", "It's out! Paused the task.", 1_000_200)])
        app.load()
        await settle(pilot)
        app.load()
        await settle(pilot)
        assert sent == ["Watch the thing: It's out! Paused the task."]
        assert any("◷ Watch the thing: It's out!" in str(n.message) for n in app._notifications)


async def test_no_routines_no_row(sessions):  # noqa: F811
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        assert not any(o.id == "R:routines" for o in app.query_one(SessionList).options)
        await pilot.press("U")
        await settle(pilot)
        assert isinstance(app.screen, Routines)
