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
        assert app.waiting == set()
        set_status(sessions, "a1", "idle")
        app.poll_live()
        app.poll_live()
        assert app.waiting == {"a1"}
        await settle(pilot)  # notifications are sent from a worker
        assert sent == ["Fix login bug is waiting"]
        row = app.row(app.by_id["a1"])
        assert row.plain.startswith("◆ ") and "bold" in str(row.spans)
        assert "1 waiting" in str(app.query_one("#status").render())
        assert "waiting for you" in "".join(str(r) for r in app.meta(app.by_id["a1"]).renderables)
        set_status(sessions, "a1", "busy")
        app.poll_live()
        assert app.waiting == set()
        await settle(pilot)


async def test_already_idle_at_start_is_not_waiting(sessions, sent):
    set_status(sessions, "a1", "idle")
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        app.poll_live()
        assert app.waiting == set() and sent == []


async def test_opening_clears_and_leaving_live_clears(sessions, monkeypatch):
    set_status(sessions, "a1", "busy")
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        set_status(sessions, "a1", "idle")
        app.poll_live()
        assert app.waiting == {"a1"}
        monkeypatch.setattr(app, "resume_flow", lambda s: None)  # opening itself is covered elsewhere
        app.resume(app.by_id["a1"])
        assert app.waiting == set()
        app.waiting.add("a1")
        (sessions.live / "1.json").unlink()
        app.poll_live()
        assert app.waiting == set()
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
        assert app.waiting == {"a1"} and sent == []


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
