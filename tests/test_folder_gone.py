import os
import shutil
import subprocess

from conftest import rec, user

from csm import data
from csm.dialogs import FolderGone
from test_app import CSM, settle, sessions  # noqa: F401  (fixture)


def resumed(app, monkeypatch):
    seen = []
    monkeypatch.setattr(app, "resume_flow", lambda s: seen.append((s.id, s.cwd)))
    return seen


async def test_a_gone_folder_is_flagged_and_can_be_put_back(sessions, tmp_path, monkeypatch):  # noqa: F811
    folder = tmp_path / "alpha"
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        seen = resumed(app, monkeypatch)
        s = app.by_id["a1"]
        assert s.origin == str(folder)
        shutil.rmtree(folder)
        assert "folder is gone" in "".join(str(r) for r in app.meta(s).renderables)
        app.resume(s)
        await settle(pilot)
        assert isinstance(app.screen, FolderGone) and app.screen.branch is None  # not a worktree: no w
        await pilot.press("w")  # nothing to re-add: ignored
        assert isinstance(app.screen, FolderGone)
        await pilot.press("c")
        await settle(pilot)
        assert seen == [] and not folder.exists()
        app.resume(s)
        await settle(pilot)
        await pilot.press("e")
        await settle(pilot)
        assert folder.is_dir() and seen == [("a1", str(folder))]


async def test_a_deleted_worktree_is_re_added_from_its_branch(paths, write, tmp_path, monkeypatch):
    repo = tmp_path / "web"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    run("init", "-q", "-b", "main")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "start")
    wt = repo / ".claude" / "worktrees" / "fix-login"
    run("worktree", "add", "-q", "-b", "fix-login", str(wt))
    write(str(wt), "w1", user("hi", str(wt), gitBranch="fix-login"), rec(type="custom-title", customTitle="Fix login"))
    run("worktree", "remove", str(wt))
    assert not wt.exists()
    app = CSM(paths)
    async with app.run_test() as pilot:
        await settle(pilot)
        seen = resumed(app, monkeypatch)
        s = next(x for x in app.sessions if x.id == "w1")
        assert s.origin == str(wt) and s.branch == "fix-login"
        app.resume(s)
        await settle(pilot)
        assert isinstance(app.screen, FolderGone) and app.screen.branch == "fix-login"
        await pilot.press("w")
        await settle(pilot)
    assert (wt / ".git").exists() and seen == [("w1", str(wt))]
    head = subprocess.run(["git", "-C", str(wt), "branch", "--show-current"], capture_output=True, text=True).stdout
    assert head.strip() == "fix-login"


async def test_a_running_session_is_not_stopped_by_the_check(sessions, tmp_path, monkeypatch):  # noqa: F811
    import json
    (sessions.live / "1.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "a1", "status": "idle"}))
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        seen = resumed(app, monkeypatch)
        shutil.rmtree(tmp_path / "alpha")
        app.resume(app.by_id["a1"])  # it runs somewhere already; that's where it opens
        await settle(pilot)
        assert not isinstance(app.screen, FolderGone) and [sid for sid, _ in seen] == ["a1"]


def test_origin_survives_a_cache_hit(paths, write, tmp_path):
    f = write(str(tmp_path / "gone-later"), "o1", user("hi", str(tmp_path / "gone-later")),
              rec(type="custom-title", customTitle="T"))
    [s] = data.load_sessions(paths)
    shutil.rmtree(tmp_path / "gone-later")
    [cached] = data.load_sessions(paths)  # unchanged transcript: served from the cache
    assert cached.origin == s.origin == str(tmp_path / "gone-later") and f.exists()


async def test_dialogs_are_centred_and_wrap(sessions, tmp_path):  # noqa: F811
    from csm.dialogs import AutoArchiveSettings, Confirm, DirPrompt, FirstMessage, Prompt, WhenIdle
    from csm import autoarchive
    long = "a very long session title that goes on " * 3
    dialogs = [FolderGone(long, "/r/web/.claude/worktrees/" + "x" * 80, "fix-login"), Confirm(long), Prompt(long),
               DirPrompt(long, "~/"), FirstMessage("~/code/" + "y" * 90), WhenIdle(long, None, True),
               AutoArchiveSettings(autoarchive.normalize(None))]
    app = CSM(sessions)
    async with app.run_test(size=(100, 40)) as pilot:
        await settle(pilot)
        for d in dialogs:
            app.push_screen(d)
            await pilot.pause()
            box = app.screen.query_one(".dialog")
            assert box.region.x > 0 and box.region.y > 0, type(d).__name__  # not stuck in the corner
            for label in app.screen.query("Label"):
                assert label.region.right <= box.region.right, type(d).__name__  # nothing cut off
            assert max(lbl.region.height for lbl in app.screen.query("Label")) >= 2 or isinstance(d, AutoArchiveSettings)
            app.pop_screen()
            await pilot.pause()
