import json
import os

from csm import data, ghostty, launch, layout
from csm.dialogs import Confirm
from test_app import CSM, FakeHost, settle, sessions  # noqa: F401  (fixture)

FLAG = "--dangerously-skip-permissions"


def test_resume_skips_permissions_only_when_asked():
    assert launch.resume("s1", "/tmp", skip_permissions=True).argv[-1] == FLAG
    assert FLAG not in launch.resume("s1", "/tmp").argv


async def test_y_asks_then_reopens_unguarded_and_remembers(sessions):  # noqa: F811
    host = FakeHost()
    app = CSM(sessions, host=host)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("Y")  # b1, not running
        await settle(pilot)
        assert isinstance(app.screen, Confirm) and FLAG in str(app.screen.message)
        await pilot.press("n")
        await settle(pilot)
        assert host.calls == [] and "b1" not in app.state.unguarded
        await pilot.press("Y")
        await settle(pilot)
        await pilot.press("y")
        await settle(pilot)
        assert host.calls[-1][:2] == ("show", "b1") and FLAG in host.calls[-1][2]
        assert "b1" in app.state.unguarded
        assert "⚠ unguarded" in app.row(app.by_id["b1"]).plain
    assert "b1" in data.State(sessions.state).unguarded


async def test_y_on_a_running_session_warns_and_launches_nothing(sessions):  # noqa: F811
    (sessions.live / "1.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "b1", "status": "idle"}))
    host = FakeHost()
    host.panes["a2"] = "%1"
    app = CSM(sessions, host=host)
    async with app.run_test() as pilot:
        await settle(pilot)
        for sid in ("b1", "a2"):  # live elsewhere, and hosted here
            app.focus_id = sid
            app.rebuild()
            assert app.selected().id == sid
            await pilot.press("Y")
            await settle(pilot)
            assert len(app.screen_stack) == 1
        assert host.calls == [] and not app.state.unguarded
        assert sum("permissions can only be skipped from the start" in str(n.message) for n in app._notifications) == 2


async def test_reopening_normally_drops_the_flag(sessions):  # noqa: F811
    st = data.State(sessions.state)
    st.unguarded.add("b1")
    st.save()
    host = FakeHost()
    app = CSM(sessions, host=host)
    async with app.run_test() as pilot:
        await settle(pilot)
        assert "⚠ unguarded" not in app.row(app.by_id["b1"]).plain  # not running: no marker
        await pilot.press("enter")
        await settle(pilot)
        assert host.calls[-1][:2] == ("show", "b1") and FLAG not in host.calls[-1][2]
        assert "b1" not in app.state.unguarded
        assert "⚠ unguarded" not in app.row(app.by_id["b1"]).plain
    assert "b1" not in data.State(sessions.state).unguarded


async def test_switching_to_a_running_session_keeps_the_flag(sessions):  # noqa: F811
    st = data.State(sessions.state)
    st.unguarded.add("b1")
    st.save()
    host = FakeHost()
    host.panes["b1"] = "%1"
    app = CSM(sessions, host=host)
    async with app.run_test() as pilot:
        await settle(pilot)
        assert "⚠ unguarded" in app.row(app.by_id["b1"]).plain
        await pilot.press("enter")  # just shows the pane it's already in
        await settle(pilot)
        assert "b1" in app.state.unguarded


async def test_ghostty_open_drops_the_flag(sessions, monkeypatch):  # noqa: F811
    opened = []
    monkeypatch.setattr(ghostty, "open_session", lambda cwd, cmd, where: opened.append(cmd))
    st = data.State(sessions.state)
    st.unguarded.add("b1")
    st.save()
    app = CSM(sessions, host=FakeHost())
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("o")
        await settle(pilot)
        assert opened and FLAG not in opened[0]
        assert "b1" not in app.state.unguarded


async def test_restore_reopens_unguarded_sessions_the_same_way(sessions):  # noqa: F811
    st = data.State(sessions.state)
    st.unguarded |= {"b1", "a1"}
    st.save()
    layout.save(sessions.state.parent / "layout.json", ["b1", "a2", "a1"], ["a2", "a1"])
    host = FakeHost()
    app = CSM(sessions, host=host)
    async with app.run_test() as pilot:
        await settle(pilot)
        cmds = {sid: cmd for _, sid, cmd in host.calls}
        assert FLAG in cmds["b1"] and FLAG in cmds["a1"] and FLAG not in cmds["a2"]
        assert "⚠ unguarded" in app.row(app.by_id["b1"]).plain
        assert "⚠ unguarded" not in app.row(app.by_id["a2"]).plain


def test_state_merge_keeps_unguarded_changes_made_elsewhere(tmp_path):
    f = tmp_path / "state.json"
    mine = data.State(f)
    mine.unguarded.add("a")
    mine.save()
    other = data.State(f)
    other.unguarded.add("b")
    other.save()
    mine.unguarded.discard("a")  # never having seen b
    mine.save()
    assert data.State(f).unguarded == {"b"}
