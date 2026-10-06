import os
import shutil
import subprocess
import time

import pytest
from conftest import rec, user

from csm import data, prs, worktrees
from csm.app import CSM
from csm.data import LiveSession, Session
from test_app import settle

DAY = 86400
NOW = time.time()


def run(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture(autouse=True)
def git_env(monkeypatch):
    for k, v in dict(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                     GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull).items():
        monkeypatch.setenv(k, v)


@pytest.fixture
def repo(tmp_path):
    """A repo pushed to a local bare origin, so unpushed counts are meaningful."""
    origin, r = tmp_path / "origin.git", tmp_path / "proj"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    r.mkdir()
    run(r, "init", "-q", "-b", "main")
    run(r, "commit", "-q", "--allow-empty", "-m", "init")
    run(r, "remote", "add", "origin", str(origin))
    run(r, "push", "-q", "origin", "main")
    return r


def add(repo, name):
    path = repo / ".claude" / "worktrees" / name
    run(repo, "worktree", "add", "-q", "-b", name, str(path))
    return path


def sess(repo, wt, id="s", mtime=NOW, pr_url=None, title="work"):
    return Session(id=id, path="", title=title, project=str(repo), cwd=str(wt), worktree=True,
                   worktrees=[str(wt)], mtime=mtime, pr_url=pr_url)


def find(found, name):
    return next(w for w in found if w.name == name)


def test_discovers_registered_and_missing_only(paths, repo):
    add(repo, "fresh")
    gone = add(repo, "gone")
    (repo / ".claude" / "worktrees" / "stray").mkdir()  # a directory git doesn't know
    run(repo, "worktree", "add", "-q", "-b", "other", str(repo.parent / "elsewhere"))
    shutil.rmtree(gone)
    found = worktrees.discover(paths, [sess(repo, repo)], {})
    assert sorted(w.name for w in found) == ["fresh", "gone"]
    assert find(found, "gone").missing and not find(found, "fresh").missing
    assert find(found, "fresh").branch == "fresh"


def test_session_worktree_dirs_are_recorded(paths, write, tmp_path):
    p = str(tmp_path / "proj")
    wt = p + data.WORKTREE_MARK + "w1"
    write(p, "a", user("hi", p), user("again", wt + "/sub"))
    [s] = data.load_sessions(paths)
    assert s.worktrees == [wt]


def test_candidates(paths, repo):
    merged, stale, fresh, busy = (add(repo, n) for n in ("merged", "stale", "fresh", "busy"))
    prs._write_json(paths.prs, {"url": {"state": "merged", "checks": "none", "review": None,
                                       "passed": 0, "total": 0, "fetched_at": NOW}})
    ss = [sess(repo, merged, "m", NOW, "url"), sess(repo, stale, "st", NOW - 20 * DAY),
          sess(repo, fresh, "f", NOW - DAY), sess(repo, busy, "b", NOW - 30 * DAY, "url")]
    live = {"b": LiveSession(pid=1, session_id="b", status="idle", entrypoint="cli")}
    found = worktrees.discover(paths, ss, live, NOW)
    assert {w.name for w in found[:2]} == {"merged", "stale"}
    assert find(found, "merged").reason == "PR merged"
    assert find(found, "stale").reason == "idle 20d"
    assert not find(found, "fresh").candidate
    assert find(found, "busy").live and not find(found, "busy").candidate


def test_live_by_cwd(paths, repo):
    wt = add(repo, "w")
    live = {"x": LiveSession(pid=1, session_id="x", status="busy", entrypoint="cli", cwd=str(wt / "sub"))}
    [w] = worktrees.discover(paths, [sess(repo, wt, mtime=NOW - 30 * DAY)], live, NOW)
    assert w.live and not w.candidate


def test_dirty_and_unpushed(paths, repo):
    add(repo, "clean")
    dirty, ahead = add(repo, "dirty"), add(repo, "ahead")
    (dirty / "a.txt").write_text("x")
    (dirty / "b.txt").write_text("y")
    run(ahead, "commit", "-q", "--allow-empty", "-m", "more")
    found = worktrees.discover(paths, [sess(repo, repo)], {})
    assert (find(found, "clean").dirty, find(found, "clean").unpushed) == (0, 0)
    assert find(found, "dirty").dirty == 2
    assert (find(found, "ahead").dirty, find(found, "ahead").unpushed) == (0, 1)


def test_remove_clean_worktree_keeps_branch(paths, repo):
    wt = add(repo, "done")
    [w] = worktrees.discover(paths, [sess(repo, wt)], {})
    assert worktrees.remove(w)[0]
    assert not wt.exists()
    branches = subprocess.run(["git", "-C", str(repo), "branch"], capture_output=True, text=True).stdout
    assert "done" in branches


def test_remove_refuses_dirty_worktree(paths, repo):
    wt = add(repo, "wip")
    (wt / "a.txt").write_text("x")
    [w] = worktrees.discover(paths, [sess(repo, wt)], {})
    assert "WARNING" in worktrees.confirm_text(w)
    ok, msg = worktrees.remove(w)
    assert not ok and msg
    assert (wt / "a.txt").exists()


def test_prune_missing(paths, repo):
    wt = add(repo, "lost")
    shutil.rmtree(wt)
    [w] = worktrees.discover(paths, [sess(repo, wt)], {})
    assert w.missing and w.candidate
    assert worktrees.remove(w)[0]
    assert worktrees.discover(paths, [sess(repo, wt)], {}) == []


async def open_screen(app, pilot):
    await settle(pilot)
    await pilot.press("W")
    assert isinstance(app.screen, worktrees.Worktrees)
    await app.workers.wait_for_complete()
    await pilot.pause()


def transcript(write, repo, wt):
    p = str(repo)
    write(p, "a1", user("hi", p), user("again", str(wt)))


async def test_W_opens_and_esc_closes(paths, write, repo):
    transcript(write, repo, add(repo, "w1"))
    app = CSM(paths)
    async with app.run_test() as pilot:
        await open_screen(app, pilot)
        assert [w.name for w in app.screen.items.values()] == ["w1"]
        await pilot.press("escape")
        assert not isinstance(app.screen, worktrees.Worktrees)
        await pilot.press("W", "W")
        assert not isinstance(app.screen, worktrees.Worktrees)


async def test_d_confirms_then_removes(paths, write, repo):
    wt = add(repo, "w1")
    transcript(write, repo, wt)
    app = CSM(paths)
    async with app.run_test() as pilot:
        await open_screen(app, pilot)
        await pilot.press("d", "y")
        await app.workers.wait_for_complete()
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert not wt.exists()
        assert app.screen.items == {}


def test_repo_state_counts_uncommitted_and_unpushed(tmp_path):
    import subprocess
    from csm.worktrees import repo_state
    def g(*a, cwd):
        subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)
    up, clone = tmp_path / "up", tmp_path / "clone"
    g("init", "-q", "--bare", "-b", "main", str(up), cwd=tmp_path)
    g("clone", "-q", str(up), str(clone), cwd=tmp_path)
    for i in range(2):
        (clone / f"f{i}").write_text("x")
        g("add", ".", cwd=clone)
        g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", f"c{i}", cwd=clone)
        if i == 0:
            g("push", "-q", "origin", "main", cwd=clone)
    (clone / "dirty").write_text("x")
    (clone / "f0").write_text("changed")
    assert repo_state(str(clone)) == (2, 1, 0)
    assert repo_state(str(tmp_path)) is None
