"""Draw every screen, dialog, toast and strip style with data built to break rendering.

Two sidebar crashes were errors raised only while drawing (a toast whose text Textual read as
markup), which no other test drew. Here everything is drawn, at the sidebar's real size and
others, with titles, tags, notes and messages full of brackets, emoji and length.
"""
import json
import os
import subprocess
import time

import pytest
from conftest import assistant, rec, user

from csm import brief, data, summary, tips
from csm.activity import STYLES, Activity
from csm.app import CSM
from test_app import FakeHost, settle

NASTY = [
    "[WIP] fix [/] the [bold]login[/bold] flow",
    "Deploy 🚀 to prod ✨ — 日本語 タイトル",
    "x" * 300,
    "[link=https://example.com]click[/link] and [red]",
    "Ends with a bracket [",
]
DIALOGS = [("I", "escape"), ("dollar_sign", "escape"), ("L", "escape"), ("t", "escape"), ("W", "escape"),
           ("A", "escape"), ("at", "escape"), ("r", "escape"), ("number_sign", "escape"), ("i", "escape"),
           ("R", "escape"), ("P", "escape"), ("n", "escape"), ("d", "escape"), ("X", "escape"), ("B", "escape")]
SIZES = [(70, 68), (140, 45), (34, 20)]  # the real sidebar, full width, cramped


@pytest.fixture
def nasty(paths, write, tmp_path):
    proj = str(tmp_path / "proj [beta]")
    other = str(tmp_path / "other")
    for i, title in enumerate(NASTY):
        cwd = proj if i % 2 else other
        lines = [user(f"start [{i}] ] / [ here", cwd), rec(type="custom-title", customTitle=title),
                 assistant(f"Done: **bold** `[code]` [/] and a [link](http://x) {title}", cwd)]
        if i == 1:
            lines.append(rec(type="pr-link", prNumber=12, prUrl="https://github.com/o/r/pull/12"))
        write(cwd, f"s{i}", *lines)
    for i, f in enumerate(sorted(paths.projects.glob("*/*.jsonl"))):
        os.utime(f, (time.time() - 600 - i * 60,) * 2)
    statuses = ["busy", "idle", "busy", "idle", "busy"]
    for i, st in enumerate(statuses):
        (paths.live / f"{i}.json").write_text(json.dumps(
            {"pid": os.getpid(), "sessionId": f"s{i}", "status": st, "kind": "bg" if i == 4 else "interactive"}))
    paths.status.mkdir(parents=True, exist_ok=True)
    (paths.status / "s3.json").write_text(json.dumps(
        {"state": "permission", "message": "Claude needs your permission to use [Bash] ] / [", "at": time.time()}))
    paths.state.write_text(json.dumps({
        "pinned": ["s2"], "tags": {"s0": ["waiting-on-[x]"], "s1": ["a"]},
        "notes": {"s0": "note with [/] and [bold]markup"}, "when_idle": {"s2": {"do": "send", "text": "[/] hi"}}}))
    digests = summary.Digests(paths.summaries)
    return paths, digests


def stub_outside_world(monkeypatch, paths, tmp_path):
    monkeypatch.setenv("CSM_RECAP_DIR", str(tmp_path / "recaps"))
    monkeypatch.setattr(summary, "refresh", lambda *a, **k: summary.Digests(paths.summaries))
    monkeypatch.setattr(brief, "write", lambda s: f"Carry on [/] with {s.title} ] / [")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))


async def draw(pilot, app):
    """Let everything render; a drawing error ends the app with return code 1."""
    await settle(pilot)
    await pilot.pause(0.15)
    assert app.return_code is None, app.crash_log.read_text() if app.crash_log.exists() else "crashed"


@pytest.mark.parametrize("size", SIZES)
async def test_everything_draws(nasty, size, monkeypatch, tmp_path):
    paths, digests = nasty
    monkeypatch.setenv("CSM_STUCK_MINUTES", "5")  # s0, s2 and s4 are busy and quiet: stuck
    for s in data.load_sessions(paths):
        digests.put(s, summary.Digest(status="needs_input", question=f"Merge [/] {s.title}?",
                                      done=["opened [PR] #12"], decisions=["use [sub]"], headline="[bold]x"))
    digests.save()
    stub_outside_world(monkeypatch, paths, tmp_path)
    host = FakeHost()
    app = CSM(paths, host=host)
    async with app.run_test(size=size, notifications=True, tooltips=True) as pilot:  # off by default in tests
        await draw(pilot, app)
        app.waiting["s1"] = time.time() - 900
        app.rebuild()
        await draw(pilot, app)

        # every row, in each layout
        for keys in (["e"], ["v"], ["v"], ["a"], ["a"], ["exclamation_mark"], ["exclamation_mark"]):
            await pilot.press(*keys)
            await draw(pilot, app)
        for _ in range(12):
            await pilot.press("down")
            await draw(pilot, app)

        # every screen and dialog, opened on a nasty session, then closed
        await pilot.press("home")
        for _ in range(3):
            await pilot.press("down")
        for opener, closer in [("question_mark", "x"), *DIALOGS]:
            await pilot.press(opener)
            await draw(pilot, app)
            if closer:
                await pilot.press(closer)
                await draw(pilot, app)
        for opener, closer in [ ("J", None), ("S", None)]:
            await pilot.press(opener)
            await draw(pilot, app)
            if closer:
                await pilot.press(closer)
                await draw(pilot, app)

        # typing brackets into the filter and the transcript search; the status bar echoes both
        await pilot.press("slash", *"[/] [bold", "escape", "s", *"[red] ] / [", "enter")
        await draw(pilot, app)
        await pilot.press("escape", "escape")
        await draw(pilot, app)

        # a worktree removal confirmation for names full of brackets
        from csm import worktrees
        from csm.dialogs import Confirm
        wt = worktrees.Worktree(repo="/r/[x]", path="/r/[x]/.claude/worktrees/[/]wip", branch="[red]b",
                                missing=False, dirty=2, unpushed=1)
        app.push_screen(Confirm(worktrees.confirm_text(wt)))
        await draw(pilot, app)
        await pilot.press("escape")
        await draw(pilot, app)

        # toasts, including every tip and every session title
        for tip in tips.TIPS:
            app.notify(f"{tip.text}\n(ctrl+t turns tips off)")
        for title in NASTY:
            app.notify(f"Sent to “{title}”")
            app.notify(f"{title} looks stuck", severity="warning")
        await draw(pilot, app)

        # every activity style, busy and easing out
        bar = app.query_one(Activity)
        for style in STYLES:
            bar.set_style(style)
            bar.busy = 3
            await pilot.pause(0.2)
            await draw(pilot, app)
    assert app.return_code in (None, 0) and not app.crash_log.exists()


async def test_every_dialog_on_every_awkward_session(nasty, monkeypatch, tmp_path):
    """Dialogs put the session's title in their labels, which Textual parses as markup."""
    paths, _ = nasty
    stub_outside_world(monkeypatch, paths, tmp_path)
    app = CSM(paths, host=FakeHost())
    async with app.run_test(size=SIZES[0], notifications=True, tooltips=True) as pilot:
        await draw(pilot, app)
        for i in range(len(NASTY)):
            app.focus_id = f"s{i}"
            app.rebuild()
            await draw(pilot, app)
            assert app.selected().title == NASTY[i]
            for opener, closer in DIALOGS:
                await pilot.press(opener)
                await draw(pilot, app)
                await pilot.press(closer)
                await draw(pilot, app)
    assert not app.crash_log.exists()
