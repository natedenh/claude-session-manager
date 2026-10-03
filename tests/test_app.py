import json
import os

import pytest
from conftest import assistant, rec, user

from csm import data
from csm.app import CSM, SessionList


@pytest.fixture
def sessions(paths, write, tmp_path):
    alpha, beta = str(tmp_path / "alpha"), str(tmp_path / "beta")
    write(alpha, "a1", user("hi", alpha), rec(type="custom-title", customTitle="Fix login bug"),
          assistant("The token expired", alpha))
    write(alpha, "a2", user("hi", alpha), rec(type="custom-title", customTitle="Add billing page"),
          rec(type="pr-link", prNumber=7, prUrl="u"))
    write(beta, "b1", user("hi", beta), rec(type="custom-title", customTitle="Beta setup"))
    for i, f in enumerate(sorted(paths.projects.glob("*/*.jsonl"))):
        os.utime(f, (1_000_000 + i,) * 2)  # a1 oldest ... b1 newest
    return paths


def ids(app):
    return [o.id for o in app.query_one(SessionList).options if o.id and o.id.startswith("s:")]


async def settle(pilot):
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


async def test_lists_grouped_newest_first(sessions):
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        assert ids(app) == ["s:b1", "s:a2", "s:a1"]
        assert app.selected().id == "b1"


async def test_filter_and_pr_toggle(sessions):
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("slash", *"login")
        assert ids(app) == ["s:a1"]
        await pilot.press("escape")
        assert len(ids(app)) == 3
        await pilot.press("p")
        assert ids(app) == ["s:a2"]


async def test_transcript_search(sessions):
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("s", *"token", "enter")
        await settle(pilot)
        assert ids(app) == ["s:a1"] and "token" in app.hits["a1"][0]


async def test_archive_hides_and_persists(sessions):
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("x")
        assert "s:b1" not in ids(app)
        await pilot.press("a")
        assert ids(app) == ["s:b1"]
    assert data.State(sessions.state).archived == {"b1"}


async def test_rename(sessions):
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("r", *"Renamed", "enter")  # the dialog preselects the old title
        await pilot.pause()
        assert app.by_id["b1"].title == "Renamed"
    assert {s.id: s.title for s in data.load_sessions(sessions)}["b1"] == "Renamed"


async def test_collapse_project(sessions):
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("up", "enter")  # header of beta
        assert ids(app) == ["s:a2", "s:a1"]


async def test_enter_resumes(sessions):
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("down", "down", "enter")  # skips the spacer, passes alpha's header
    assert app.return_value.id == "a2"


async def test_live_session_asks_before_resuming(sessions):
    (sessions.live / "1.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "b1", "status": "idle"}))
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await pilot.pause()
        assert app.return_value is None  # waiting on the confirm dialog
        await pilot.press("n")
        await pilot.press("enter", "y")
    assert app.return_value.id == "b1"
