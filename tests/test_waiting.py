import json
import os

from test_app import ids, sessions, settle  # noqa: F401

from csm import notify
from csm.notify import send as real_send
from csm.app import CSM


def set_status(paths, sid, status):
    (paths.live / "1.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": sid, "status": status}))


async def test_busy_to_idle_marks_waiting_and_notifies_once(sessions, sent):
    set_status(sessions, "a1", "busy")
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        assert set(app.waiting) == set()
        set_status(sessions, "a1", "idle")
        app.poll_live()
        app.poll_live()
        assert set(app.waiting) == {"a1"}
        await settle(pilot)  # notifications are sent from a worker
        assert sent == ["Fix login bug is waiting"]
        row = app.row(app.by_id["a1"])
        assert row.plain.startswith("◆ ") and "bold" in str(row.spans)
        assert "1 waiting" in str(app.query_one("#status").render())
        assert "waiting for you" in "".join(str(r) for r in app.meta(app.by_id["a1"]).renderables)
        set_status(sessions, "a1", "busy")
        app.poll_live()
        assert set(app.waiting) == set()
        await settle(pilot)


async def test_already_idle_at_start_is_not_waiting(sessions, sent):
    set_status(sessions, "a1", "idle")
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        app.poll_live()
        assert set(app.waiting) == set() and sent == []


async def test_opening_clears_and_leaving_live_clears(sessions, monkeypatch):
    set_status(sessions, "a1", "busy")
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        set_status(sessions, "a1", "idle")
        app.poll_live()
        assert set(app.waiting) == {"a1"}
        monkeypatch.setattr(app, "resume_flow", lambda s: None)  # opening itself is covered elsewhere
        app.resume(app.by_id["a1"])
        assert set(app.waiting) == set()
        app.waiting["a1"] = 0.0
        (sessions.live / "1.json").unlink()
        app.poll_live()
        assert set(app.waiting) == set()
        await settle(pilot)


async def test_waiting_filter(sessions):
    set_status(sessions, "a1", "busy")
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        set_status(sessions, "a1", "idle")
        app.poll_live()
        await pilot.press("exclamation_mark")
        assert ids(app) == ["s:a1"]
        await pilot.press("exclamation_mark")
        assert len(ids(app)) == 3


async def test_no_notification_for_shown_session(sessions, sent):
    set_status(sessions, "a1", "busy")
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        app.shown_id = "a1"
        set_status(sessions, "a1", "idle")
        app.poll_live()
        assert sent == []


async def test_notifications_disabled(sessions, sent):
    set_status(sessions, "a1", "busy")
    app = CSM(sessions, notifications=False)
    async with app.run_test() as pilot:
        await settle(pilot)
        set_status(sessions, "a1", "idle")
        app.poll_live()
        assert set(app.waiting) == {"a1"} and sent == []


def test_send_writes_osc9_to_client_tty(monkeypatch, tmp_path):
    tty = tmp_path / "tty"
    tty.touch()

    class R:
        stdout = f"{tty}\n"
    monkeypatch.setenv("TMUX", "x")
    monkeypatch.setattr(notify.subprocess, "run", lambda *a, **k: R())
    real_send("hi\x1b there")
    assert tty.read_text() == "\033]9;hi there\a"


def test_send_fails_silently(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("builtins.open", lambda *a, **k: (_ for _ in ()).throw(OSError))
    real_send("x")


async def test_waiting_shows_how_long_and_redraws_as_it_grows(sessions, sent):
    import time
    from csm.app import SessionList, waited
    assert waited(time.time() - 5) == "1m" and waited(time.time() - 25 * 60) == "25m"
    assert waited(time.time() - 3 * 3600) == "3h" and waited(time.time() - 2 * 86400) == "2d"
    set_status(sessions, "a1", "idle")
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        app.waiting["a1"] = time.time() - 25 * 60
        app.rebuild()
        await settle(pilot)
        assert app.row(app.by_id["a1"]).plain.startswith("◆ ○ 25m Fix login bug")
        assert "1 waiting (longest 25m)" in str(app.query_one("#status").render())
        assert "waiting for you for 25m" in "".join(str(r) for r in app.meta(app.by_id["a1"]).renderables)
        app.waiting["a1"] = time.time() - 2 * 3600  # time passes; the poll notices the label changed
        app.poll_live()
        await settle(pilot)
        assert "2h Fix login bug" in app.query_one(SessionList).get_option("s:a1").prompt.plain


async def test_tab_opens_the_longest_waiting_then_the_next(sessions, sent, monkeypatch):
    import time
    from csm.app import SessionList
    for i, sid in enumerate(("a1", "a2", "b1")):
        (sessions.live / f"{i}.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": sid, "status": "idle"}))
    app = CSM(sessions)
    opened = []
    async with app.run_test() as pilot:
        await settle(pilot)
        monkeypatch.setattr(app, "resume_flow", lambda s: opened.append(s.id))
        await pilot.press("tab")
        assert opened == [] and app.trail == []  # nothing waiting
        now = time.time()
        app.waiting.update({"a1": now - 60, "b1": now - 600})
        app.permission, app.permission_at = {"a2": "Bash"}, {"a2": now - 5}
        await pilot.press("tab")
        await pilot.press("tab")
        await pilot.press("tab")
        await settle(pilot)
        assert opened == ["a2", "b1", "a1"]  # permission first, then the longest wait
        assert app.query_one(SessionList).highlighted_option.id == "s:a1"
        await pilot.press("tab")
        assert len(opened) == 3  # all handled
        await pilot.press("shift+tab")
        await settle(pilot)
        assert opened[-1] == "b1" and app.query_one(SessionList).highlighted_option.id == "s:b1"


async def test_busy_and_quiet_looks_stuck_once(sessions, sent, monkeypatch):
    import time
    monkeypatch.setenv("CSM_STUCK_MINUTES", "10")
    set_status(sessions, "a1", "busy")
    path = sessions.projects / next(p for p in os.listdir(sessions.projects) if "alpha" in p) / "a1.jsonl"
    os.utime(path, (time.time(),) * 2)  # just wrote: not stuck
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        assert app.stuck == {}
        os.utime(path, (time.time() - 14 * 60,) * 2)
        app.poll_live()
        app.poll_live()
        await settle(pilot)
        assert set(app.stuck) == {"a1"} and sent == ["Fix login bug looks stuck: nothing written for 14m"]
        assert app.row(app.by_id["a1"]).plain.startswith("⧗ ○ 14m Fix login bug")
        assert "1 stuck" in str(app.query_one("#status").render())
        assert "looks stuck" in "".join(str(r) for r in app.meta(app.by_id["a1"]).renderables)
        await pilot.press("exclamation_mark")
        await settle(pilot)
        assert ids(app) == ["s:a1"]
        os.utime(path, (time.time(),) * 2)  # it wrote again
        app.poll_live()
        assert app.stuck == {}
        set_status(sessions, "a1", "idle")  # idle sessions are never stuck
        os.utime(path, (time.time() - 3600,) * 2)
        app.poll_live()
        assert app.stuck == {} and len(sent) == 1
