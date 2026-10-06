import os
import time

from csm import data, summary
from csm.data import LiveSession, Session
from csm.summary import Digest, Digests

NOW = 1_800_000_000.0


def sess(id, ago=3600, title=None):
    return Session(id=id, path="x", title=title or id, project="/p", cwd="/p", mtime=NOW - ago, size=10)


class FakeClient:
    def __init__(self, digest=None, error=None):
        self.calls, self.digest, self.error = [], digest, error
        self.messages = self

    def parse(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return type("R", (), {"stop_reason": "end_turn", "parsed_output": self.digest})()


def test_due_skips_old_unsettled_busy_and_fresh(tmp_path):
    d = Digests(tmp_path / "s.json")
    fresh = sess("fresh")
    d.put(fresh, Digest(status="idle"))
    ss = [sess("ok"), sess("old", ago=49 * 3600), sess("moving", ago=10), sess("busy"), fresh]
    live = {"busy": LiveSession(1, "busy", "busy", "cli")}
    assert [s.id for s in summary.due(ss, d, live, NOW)] == ["ok"]


def test_refresh_caches_and_backs_off_after_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(summary, "render_turns", lambda s: "turns")
    paths = data.Paths(summaries=tmp_path / "s.json")
    client = FakeClient(Digest(status="done", done=["PR #1 merged"]))
    summary.refresh(paths, [sess("a")], {}, now=NOW, client=client)
    assert len(client.calls) == 1 and Digests(paths.summaries).get(sess("a")).done == ["PR #1 merged"]
    summary.refresh(paths, [sess("a")], {}, now=NOW, client=client)
    assert len(client.calls) == 1  # unchanged transcript: served from the cache

    failing = FakeClient(error=RuntimeError("ExpiredTokenException"))
    summary.refresh(paths, [sess("b")], {}, now=NOW, client=failing)
    assert "ExpiredToken" in Digests(paths.summaries).error
    summary.refresh(paths, [sess("b")], {}, now=NOW + 60, client=failing)
    assert len(failing.calls) == 1  # backing off
    summary.refresh(paths, [sess("b")], {}, now=NOW + summary.RETRY_AFTER + 1, client=client)
    assert Digests(paths.summaries).error is None


def test_build_sections(tmp_path):
    d = Digests(tmp_path / "s.json")
    q, done, run, perm, old = sess("q"), sess("done"), sess("run"), sess("perm"), sess("old", ago=50 * 3600)
    d.put(q, Digest(status="needs_input", question="Open the issue?", decisions=["keep sub"]))
    d.put(done, Digest(status="done", done=["PR #5 merged"]))
    d.put(old, Digest(status="done", done=["ancient"]))
    live = {"run": LiveSession(1, "run", "busy", "cli")}
    page = summary.build([q, done, run, perm, old], live, set(), {"perm": "use Bash"}, d,
                         last_said=lambda s: "said", now=NOW)
    assert [i.text for i in page.needs_you] == ["Open the issue?", "needs permission: use Bash"]
    assert [i.text for i in page.decisions] == ["keep sub"]
    assert [i.text for i in page.finished] == ["PR #5 merged"]
    assert [i.text for i in page.running] == ["said"]
    assert page.pending == 2  # run and perm have no digest yet
    assert summary.counts(page) == "2 need you · 1 done · summarizing 2…"


def test_waiting_session_without_digest_uses_last_message(tmp_path):
    page = summary.build([sess("w")], {}, {"w"}, {}, Digests(tmp_path / "s.json"),
                         last_said=lambda s: "Should I push?", now=NOW)
    assert [i.text for i in page.needs_you] == ["Should I push?"]


async def test_summary_row_preview_and_screen(paths, write, tmp_path):
    from conftest import rec, user
    from csm.app import CSM, SessionList
    repo = str(tmp_path / "repo")
    f = write(repo, "a1", user("hi", repo), rec(type="custom-title", customTitle="Alpha work"))
    os.utime(f, (time.time() - 3600,) * 2)
    s = data.load_sessions(paths)[0]
    d = Digests(paths.summaries)
    d.put(s, Digest(status="needs_input", question="Merge it?"))
    d.save()
    app = CSM(paths)
    async with app.run_test(size=(140, 30)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        lst = app.query_one(SessionList)
        assert lst.options[0].id == "S:summary" and "1 need you" in lst.options[0].prompt.plain
        lst.highlighted = 0
        await pilot.pause()
        from rich.console import Console
        console = Console(width=120, record=True, file=open(os.devnull, "w"))
        console.print(summary.render(app.summary_page(), lambda t: "1h ago"))
        assert "Merge it?" in console.export_text()
        await pilot.press("S")
        await pilot.pause()
        assert type(app.screen).__name__ == "SummaryScreen"
        await pilot.press("enter")  # opens the item's session
        await pilot.pause()
    assert app.return_value.focus_id == "a1"
