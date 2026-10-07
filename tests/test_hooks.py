import io
import json
import os
import subprocess
import sys
import time

import pytest
from test_app import ids, sessions, settle  # noqa: F401

from csm import hooks
from csm.app import CSM


def run_hook(monkeypatch, paths, payload):
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    monkeypatch.setenv("XDG_STATE_HOME", str(paths.status.parent.parent))
    return hooks.run_hook()


@pytest.mark.parametrize("payload, state", [
    ({"hook_event_name": "Notification", "message": "Claude needs your permission to use Bash"}, "permission"),
    ({"hook_event_name": "Notification", "message": "Claude is waiting for your input"}, "waiting"),
    ({"hook_event_name": "Stop"}, "waiting"),
    ({"hook_event_name": "UserPromptSubmit"}, "working"),
])
def test_hook_writes_status(monkeypatch, paths, capsys, payload, state):
    assert run_hook(monkeypatch, paths, {"session_id": "s1", "cwd": "/x", **payload}) == 0
    f = paths.status.parent.parent / "csm" / "status" / "s1.json"
    d = json.loads(f.read_text())
    assert d["state"] == state and d["event"] == payload["hook_event_name"]
    assert d["message"] == payload.get("message") and abs(d["at"] - time.time()) < 5
    assert capsys.readouterr().out == ""
    assert [p.name for p in f.parent.iterdir()] == ["s1.json"]


def test_session_end_removes_file(monkeypatch, paths):
    run_hook(monkeypatch, paths, {"session_id": "s1", "hook_event_name": "Stop"})
    run_hook(monkeypatch, paths, {"session_id": "s1", "hook_event_name": "SessionEnd"})
    assert list((paths.status.parent.parent / "csm" / "status").iterdir()) == []


@pytest.mark.parametrize("stdin", ["", "not json", "[]", '{"session_id": "s1"}', '{"hook_event_name": "Stop"}',
                                   '{"session_id": "../x", "hook_event_name": "Stop"}'])
def test_hook_never_fails(monkeypatch, paths, capsys, stdin):
    assert run_hook(monkeypatch, paths, stdin) == 0
    assert not (paths.status.parent.parent / "csm" / "status").exists() or not list(
        (paths.status.parent.parent / "csm" / "status").iterdir())
    assert capsys.readouterr().out == ""


def test_module_entry_point(tmp_path):
    env = {**os.environ, "XDG_STATE_HOME": str(tmp_path)}
    r = subprocess.run([sys.executable, "-m", "csm", "hook"], input='{"session_id":"z","hook_event_name":"Stop"}',
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0 and r.stdout == ""
    assert json.loads((tmp_path / "csm" / "status" / "z.json").read_text())["state"] == "waiting"


# ---- install / uninstall ----------------------------------------------------

EXISTING = {
    "model": "opus",
    "permissions": {"allow": ["Bash(ls)"]},
    "hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": "say done"}]}],
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "check.sh"}]}],
    },
}


def cli(*args):
    return hooks.hooks_main(list(args))


def test_install_merges_and_is_idempotent(tmp_path, capsys):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps(EXISTING))
    assert cli("install", "--yes", "--settings", str(f)) == 0
    out = capsys.readouterr().out
    cfg = json.loads(f.read_text())
    assert cfg["model"] == "opus" and cfg["permissions"] == EXISTING["permissions"]
    assert cfg["hooks"]["PreToolUse"] == EXISTING["hooks"]["PreToolUse"]
    stop = cfg["hooks"]["Stop"]
    assert stop[0] == EXISTING["hooks"]["Stop"][0] and len(stop) == 2
    assert stop[1] == {"hooks": [{"type": "command", "command": hooks.hook_command()}]}
    for ev in hooks.EVENTS:
        assert f"added {ev}" in out
    backups = list(tmp_path.glob("settings.json.bak-csm-*"))
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == EXISTING

    assert cli("install", "--yes", "--settings", str(f)) == 0
    assert "nothing to change" in capsys.readouterr().out
    assert json.loads(f.read_text()) == cfg and len(list(tmp_path.glob("settings.json.bak-csm-*"))) == 1


def test_install_creates_missing_file_and_confirms(tmp_path, monkeypatch, capsys):
    f = tmp_path / "sub" / "settings.json"
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert cli("install", "--settings", str(f)) == 1
    assert not f.exists()
    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert cli("install", "--settings", str(f)) == 0
    assert set(json.loads(f.read_text())["hooks"]) == set(hooks.EVENTS)


def test_uninstall_removes_only_ours(tmp_path, capsys):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps(EXISTING))
    cli("install", "--yes", "--settings", str(f))
    assert "installed" in (cli("status", "--settings", str(f)), capsys.readouterr().out)[1]
    assert cli("uninstall", "--settings", str(f)) == 0
    assert json.loads(f.read_text()) == EXISTING
    assert cli("uninstall", "--settings", str(f)) == 0
    assert "nothing to change" in capsys.readouterr().out


def test_uninstall_drops_empty_hooks_key(tmp_path):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"model": "x"}))
    cli("install", "--yes", "--settings", str(f))
    cli("uninstall", "--settings", str(f))
    assert json.loads(f.read_text()) == {"model": "x"}


def test_bad_settings_untouched(tmp_path, capsys):
    f = tmp_path / "settings.json"
    f.write_text("{oops")
    assert cli("install", "--yes", "--settings", str(f)) == 1
    assert f.read_text() == "{oops" and not list(tmp_path.glob("*.bak*"))


# ---- in the app -------------------------------------------------------------

def set_hook(paths, sid, state, message=None, at=None):
    paths.status.mkdir(exist_ok=True)
    (paths.status / f"{sid}.json").write_text(json.dumps(
        {"event": "x", "state": state, "message": message, "at": at or time.time()}))


def set_live(paths, sid, status="busy"):
    (paths.live / "1.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": sid, "status": status}))


async def test_permission_state(sessions, sent):
    set_live(sessions, "a1")
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        set_hook(sessions, "a1", "permission", "Claude needs your permission to use Bash")
        app.poll_live()
        await settle(pilot)
        assert sent == ["Fix login bug needs permission"]
        row = app.row(app.by_id["a1"])
        assert row.plain.startswith("? ") and "red" in str(row.spans)
        assert "1 need permission" in str(app.query_one("#status").render())
        assert "needs permission (1m): Claude needs your permission to use Bash" in "".join(
            str(r) for r in app.meta(app.by_id["a1"]).renderables)
        app.poll_live()
        await settle(pilot)
        assert sent == ["Fix login bug needs permission"]
        await pilot.press("exclamation_mark")
        await settle(pilot)
        assert ids(app) == ["s:a1"]


async def test_hook_waiting_is_immediate_and_opening_clears(sessions, sent, monkeypatch):
    set_live(sessions, "a1", "busy")
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        set_hook(sessions, "a1", "waiting")
        app.poll_live()
        await settle(pilot)
        assert set(app.waiting) == {"a1"} and sent == ["Fix login bug is waiting"]
        app.poll_live()
        assert set(app.waiting) == {"a1"}
        monkeypatch.setattr(app, "resume_flow", lambda s: None)
        app.resume(app.by_id["a1"])
        app.poll_live()
        assert set(app.waiting) == set() and app.permission == {}
        set_hook(sessions, "a1", "working")
        app.poll_live()
        assert set(app.waiting) == set()


async def test_hook_ignored_when_not_live_and_absent(sessions, sent):
    set_hook(sessions, "a1", "permission", "x")
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        app.poll_live()
        assert app.permission == {} and set(app.waiting) == set() and sent == []
        assert "permission" not in str(app.query_one("#status").render())
