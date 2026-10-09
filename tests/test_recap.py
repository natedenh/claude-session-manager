from datetime import date

from csm import links, recap, summary
from csm.data import Session
from test_app import sessions  # noqa: F401  (fixture)

DAY = date(2026, 10, 7)


def s(sid, title, project, **kw):
    return Session(id=sid, path="", title=title, project=project, cwd=project, **kw)


def test_recap_groups_by_project_with_digests_and_fallbacks(tmp_path):
    digests = summary.Digests(tmp_path / "d.json")
    a = s("a", "Fix login", "/p/web", active={"2026-10-07": 90, "2026-10-06": 10}, day_cost={"2026-10-07": 3.0},
          pr_number=12, pr_url="https://x/pull/12", branch="fix-login")
    b = s("b", "Write docs", "/p/web", active={"2026-10-07": 20})
    c = s("c", "Try idea", "/p/lab", active={"2026-10-07": 30}, day_unpriced={"2026-10-07": 1000.0},
          day_cost={"2026-10-01": 2.0}, priced_weight=1000.0)
    old = s("d", "Last week", "/p/web", active={"2026-09-30": 60})
    digests.put(a, summary.Digest(status="needs_input", question="Merge it?", done=["opened PR #12"],
                                  decisions=["use sub as the key"], headline="Login fix ready"))
    text = recap.render([a, b, c, old], DAY, digests, lambda x: "I updated the README" if x.id == "b" else None,
                        lambda x: "open", set())
    assert "date: 2026-10-07" in text and "sessions: 3" in text and "cost: 5.00" in text  # c: 1000 x $2/1000
    assert "2h 20m of Claude time across 3 sessions in 2 projects" in text and "2-day streak" in text
    assert "- **Fix login** (web): Merge it?" in text
    assert text.index("## web · 1h 50m · $3.00") < text.index("## lab · 30m · $2.00")
    assert "1h 30m · $3.00 · [PR #12](https://x/pull/12) open · `fix-login`" in text
    assert "*Login fix ready*" in text and "- Done: opened PR #12" in text and "- Decided: use sub as the key" in text
    assert "- Last: I updated the README" in text and "Last week" not in text


def test_quiet_day_and_write(tmp_path):
    text = recap.render([], DAY, summary.Digests(tmp_path / "d.json"), lambda x: None)
    assert "No Claude activity recorded" in text
    path = recap.write(text, DAY, tmp_path / "out")
    assert path.name == "2026-10-07 Claude recap.md" and path.read_text() == text


async def test_J_writes_and_opens_todays_recap(sessions, monkeypatch, tmp_path):
    import subprocess
    from datetime import datetime
    from csm.app import CSM
    from test_app import settle
    monkeypatch.setenv("CSM_RECAP_DIR", str(tmp_path / "notes"))
    monkeypatch.setattr(summary, "refresh", lambda paths, ss, live, **kw: summary.Digests(tmp_path / "d.json"))
    opened = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: opened.append(cmd))
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        app.by_id["a1"].active = {datetime.now().date().isoformat(): 25}
        await pilot.press("J")
        await app.workers.wait_for_complete()
        await settle(pilot)
    [path] = (tmp_path / "notes").iterdir()
    assert "Fix login bug" in path.read_text() and [links.OPENER, str(path)] in opened

