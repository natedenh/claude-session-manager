import json
import os
import shutil

from csm import layout
from test_app import CSM, FakeHost, settle, sessions  # noqa: F401  (fixture)


def saved(paths):
    return layout.load(paths.state.parent / "layout.json")


async def test_open_sessions_are_remembered(sessions):  # noqa: F811
    host = FakeHost()
    app = CSM(sessions, host=host)
    async with app.run_test() as pilot:
        await settle(pilot)
        app.resume(app.by_id["a1"])
        await settle(pilot)
        app.poll_host()
        assert saved(sessions) == {"hosted": ["a1"], "shown": ["a1"]}
        host.panes.clear()
        host.also, host.visible = [], None
        app.poll_host()  # you closed it: nothing to reopen
        assert saved(sessions) == {"hosted": [], "shown": []}


async def test_after_a_reboot_the_layout_comes_back(sessions):  # noqa: F811
    layout.save(sessions.state.parent / "layout.json", ["b1", "a2", "a1"], ["a2", "a1"])
    host = FakeHost()  # a fresh server: nothing running
    app = CSM(sessions, host=host)
    async with app.run_test() as pilot:
        await settle(pilot)
        kinds = [(k, sid) for k, sid, _ in host.calls]
        assert kinds == [("start", "b1"), ("show", "a2"), ("show_also", "a1")]  # hidden first, then beside, in order
        assert all("-r" in cmd for _, _, cmd in host.calls)
        assert any("Reopened the 3 sessions you had open" in str(n.message) for n in app._notifications)
        assert saved(sessions)["hosted"] and set(saved(sessions)["shown"]) == {"a2", "a1"}


async def test_restore_skips_what_it_cant_reopen_and_can_be_turned_off(sessions, tmp_path, monkeypatch):  # noqa: F811
    (sessions.live / "1.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "b1", "status": "idle"}))
    shutil.rmtree(tmp_path / "alpha")  # a1 and a2's folder
    layout.save(sessions.state.parent / "layout.json", ["b1", "a1", "gone"], ["a1"])
    host = FakeHost()
    app = CSM(sessions, host=host)
    async with app.run_test() as pilot:
        await settle(pilot)
        assert host.calls == []  # b1 runs elsewhere, a1's folder is gone, "gone" has no transcript
    layout.save(sessions.state.parent / "layout.json", ["b1"], [])
    (sessions.live / "1.json").unlink()
    monkeypatch.setenv("CSM_RESTORE", "0")
    host = FakeHost()
    app = CSM(sessions, host=host)
    async with app.run_test() as pilot:
        await settle(pilot)
        assert host.calls == []


async def test_a_running_server_is_left_alone(sessions):  # noqa: F811
    layout.save(sessions.state.parent / "layout.json", ["b1", "a2"], ["a2"])
    host = FakeHost()
    host.panes["a1"] = "%9"  # csm restarted, its sessions still running: not a reboot
    app = CSM(sessions, host=host)
    async with app.run_test() as pilot:
        await settle(pilot)
        assert host.calls == []
        assert saved(sessions) == {"hosted": ["a1"], "shown": []}  # what's running now is remembered straight away
