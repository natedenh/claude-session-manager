import json
import time

from test_app import ids, sessions, settle  # noqa: F401 (sessions is a fixture)
from textual.widgets import Input, Switch

from csm import autoarchive, data, prs
from csm.app import CSM
from csm.dialogs import AutoArchiveSettings
from csm.data import LiveSession

NOW = 100 * 86400
ON = {"enabled": True, "merged_days": 7, "idle_days": 30}


def reason(rule=ON, mtime=NOW, merged_at=None, exempt=False):
    return autoarchive.reason(rule, mtime, merged_at, NOW, exempt)


def test_off_changes_nothing():
    assert reason({**ON, "enabled": False}, mtime=0, merged_at=0) is None


def test_merged_threshold():
    assert reason(merged_at=NOW - 7 * 86400) is None
    assert reason(merged_at=NOW - 9 * 86400 - 5) == "auto: PR merged 9d ago"


def test_idle_threshold():
    assert reason(mtime=NOW - 30 * 86400) is None
    assert reason(mtime=NOW - 45 * 86400) == "auto: idle 45d"


def test_exempt():
    assert reason(mtime=0, merged_at=0, exempt=True) is None


def test_normalize_old_and_junk():
    assert autoarchive.normalize(None) == autoarchive.DEFAULT
    assert autoarchive.normalize({"enabled": True, "idle_days": "x", "merged_days": 0}) == {**ON, "enabled": True}


def test_old_state_file_loads(tmp_path):
    f = tmp_path / "state.json"
    f.write_text(json.dumps({"archived": ["a"], "pinned": ["p"]}))
    st = data.State(f)
    assert st.archived == {"a"} and st.keep == set() and st.auto_archive == autoarchive.DEFAULT
    st.auto_archive = ON
    st.keep = {"k"}
    st.save()
    st = data.State(f)
    assert st.auto_archive == ON and st.keep == {"k"}


def test_merged_at_parsed_and_old_cache_loads():
    st = prs.parse({"state": "MERGED", "mergedAt": "2026-09-01T00:00:00Z"})
    assert st.merged_at == 1788220800
    assert prs.parse({"state": "OPEN"}).merged_at is None
    old = {"state": "merged", "checks": "none", "review": None, "passed": 0, "total": 0, "fetched_at": 1.0}
    assert prs.PRStatus(**old).merged_at is None


def sess(app, sid):
    return next(s for s in app.sessions if s.id == sid)


def enable(paths, **extra):
    paths.state.write_text(json.dumps({"auto_archive": ON, **extra}))


async def test_rule_off_shows_everything(sessions):
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        assert len(ids(app)) == 3
        assert "auto-archived" not in str(app.query_one("#status").render())


async def test_rule_exemptions_and_status_count(sessions):
    enable(sessions, pinned=["a1"], keep=["a2"])
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        assert ids(app) == ["s:a1", "s:a2"]  # b1 hidden; pinned a1 and kept a2 exempt
        assert "1 auto-archived" in str(app.query_one("#status").render())
        app.live = {"b1": LiveSession(1, "b1", "idle", "cli")}
        assert app.archived_by(sess(app, "b1")) is None
        app.live = {}
        await pilot.press("a")
        assert len(ids(app)) == 3
        assert app.archived_by(sess(app, "b1")) == "auto"
        assert app.auto_reason(sess(app, "b1")).startswith("auto: idle ")


async def test_merged_pr_hides(sessions):
    enable(sessions)
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        app.state.auto_archive = {**ON, "idle_days": 100000}
        app.rebuild()
        assert len(ids(app)) == 3
        app.set_pr_status({"u": prs.PRStatus("merged", "none", None, 0, 0, time.time(), time.time() - 9 * 86400)})
        assert "s:a2" not in ids(app)
        assert app.auto_reason(sess(app, "a2")) == "auto: PR merged 9d ago"
        await settle(pilot)


async def test_x_keeps_then_archives(sessions):
    enable(sessions)
    app = CSM(sessions, show_archived=True)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("x")  # highlighted b1 is auto-archived
        assert app.state.keep == {"b1"} and app.state.archived == set()
        assert app.archived_by(sess(app, "b1")) is None
        await pilot.press("x")
        assert app.state.archived == {"b1"} and app.archived_by(sess(app, "b1")) == "csm"
    assert data.State(sessions.state).keep == {"b1"}


async def test_settings_dialog_saves(sessions):
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("A")
        assert isinstance(app.screen, AutoArchiveSettings)
        app.screen.query_one("#enabled", Switch).value = True
        app.screen.query_one("#merged_days", Input).value = "3"
        app.screen.query_one("#idle_days", Input).value = "60"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.state.auto_archive == {"enabled": True, "merged_days": 3, "idle_days": 60}
        assert ids(app) == []
    assert data.State(sessions.state).auto_archive == {"enabled": True, "merged_days": 3, "idle_days": 60}


async def test_settings_dialog_escape_cancels(sessions):
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("A", "escape")
        assert app.state.auto_archive == autoarchive.DEFAULT
