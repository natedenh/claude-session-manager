import json
import os
import subprocess

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


async def test_open_in_ghostty_scripts_running_app(sessions, monkeypatch):
    calls = []
    monkeypatch.setattr("csm.app.subprocess.run",
                        lambda argv, **kw: calls.append((argv, kw)) or subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr("csm.app.shutil.which", lambda _: "/bin/claude")
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("o")
        await settle(pilot)
    [(argv, kw)] = calls
    assert argv[:2] == ["osascript", "-"] and argv[3:] == ["/bin/claude -r b1", "tab"]
    assert argv[2].endswith("/beta") and "new tab in front window" in kw["input"]


async def test_live_session_in_ghostty_tab_is_focused(sessions, monkeypatch, tmp_path):
    from csm import ghostty
    (sessions.live / "1.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "b1", "status": "idle"}))
    focused = []
    monkeypatch.setattr(ghostty, "running", lambda: True)
    monkeypatch.setattr(ghostty, "terminals", lambda: [ghostty.Terminal("T1", str(tmp_path / "beta"), "◐ Beta setup")])
    monkeypatch.setattr(ghostty, "focus", lambda tid: focused.append(tid) or True)
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        assert focused == ["T1"] and len(app.screen_stack) == 1  # no confirm dialog
    assert app.return_value is None


class FakeHost:
    def __init__(self):
        self.calls, self.panes, self.visible, self.own = [], {}, None, True

    def hosted(self):
        return dict(self.panes)

    def shown(self):
        return self.visible

    def show(self, sid, cwd, command, name=""):
        self.calls.append(("show", sid, command))
        self.panes.setdefault(sid, f"%{len(self.panes) + 1}")
        self.visible = sid

    def close(self, sid):
        self.calls.append(("close", sid))
        self.panes.pop(sid, None)
        self.visible = None if self.visible == sid else self.visible

    def detach(self):
        self.calls.append(("detach",))


async def test_host_mode_shows_beside_list_and_detaches(sessions):
    host = FakeHost()
    app = CSM(sessions, host=host)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        assert host.calls[0][:2] == ("show", "b1") and host.calls[0][2].endswith("-r b1")
        assert app.shown_id == "b1" and app.query_one(SessionList).highlighted_option.prompt.plain.startswith("▶")
        await pilot.press("c")
        await settle(pilot)
        assert ("close", "b1") in host.calls and app.shown_id is None
        await pilot.press("q")
        assert host.calls[-1] == ("detach",)
        assert app.is_running  # sessions keep running; csm stays up for the next attach


async def test_narrow_hides_preview(sessions):
    app = CSM(sessions)
    async with app.run_test(size=(60, 30)) as pilot:
        await settle(pilot)
        assert not app.query_one("#right").display
    app = CSM(sessions)
    async with app.run_test(size=(140, 30)) as pilot:
        await settle(pilot)
        assert app.query_one("#right").display


async def test_live_desktop_session_opens_in_desktop_app(sessions, monkeypatch):
    (sessions.live / "1.json").write_text(json.dumps({
        "pid": os.getpid(), "sessionId": "b1", "status": "idle",
        "entrypoint": "claude-desktop-3p", "hostSessionId": "local_5080e996-675e"}))
    calls = []
    monkeypatch.setattr("csm.desktop.subprocess.run", lambda argv, **kw: calls.append(argv))
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        assert len(app.screen_stack) == 1  # no confirm dialog
    assert calls == [["open", "claude://code/continue?session=local_5080e996-675e"]]
    assert app.return_value is None


async def test_desktop_session_with_bad_id_falls_back_to_confirm(sessions, monkeypatch):
    (sessions.live / "1.json").write_text(json.dumps({
        "pid": os.getpid(), "sessionId": "b1", "status": "idle",
        "entrypoint": "claude-desktop-3p", "hostSessionId": "local_x&evil=1"}))
    monkeypatch.setattr("csm.desktop.subprocess.run", lambda *a, **k: pytest.fail("opened a URL"))
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        assert len(app.screen_stack) == 2  # confirm dialog
