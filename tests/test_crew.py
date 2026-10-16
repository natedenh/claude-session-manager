from datetime import date

from csm import agents, crew, stats
from csm.dialogs import TeamSetup
from test_app import CSM, settle, sessions  # noqa: F401  (fixture)


def test_the_plan_never_overwrites(tmp_path):
    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "coder.md").write_text("mine")
    (tmp_path / "CLAUDE.md").write_text("# Project\nNotes.")
    steps = crew.plan(str(tmp_path))
    assert [(st.path.name, st.action) for st in steps] == [
        ("scout.md", "create"), ("coder.md", "skip"), ("tester.md", "create"), ("CLAUDE.md", "append")]
    written = crew.apply(steps)
    assert [p.name for p in written] == ["scout.md", "tester.md", "CLAUDE.md"]
    assert (tmp_path / ".claude" / "agents" / "coder.md").read_text() == "mine"
    scout = (tmp_path / ".claude" / "agents" / "scout.md").read_text()
    assert scout.startswith("---\nname: scout\n") and "\nmodel: haiku\n" in scout
    md = (tmp_path / "CLAUDE.md").read_text()
    assert md.startswith("# Project\nNotes.\n") and md.count(crew.SECTION) == 1
    assert [st.action for st in crew.plan(str(tmp_path))] == ["skip"] * 4  # run again: nothing to do
    assert crew.apply(crew.plan(str(tmp_path))) == []


def test_every_agent_is_pinned_to_a_cheaper_model():
    pins = {name: next(l for l in text.splitlines() if l.startswith("model:")) for name, text in crew.AGENTS.items()}
    assert pins == {"scout": "model: haiku", "coder": "model: sonnet", "tester": "model: haiku"}


def test_the_tester_cannot_edit():
    assert "\ntools: Read, Grep, Glob, Bash\n" in crew.AGENTS["tester"]


async def test_T_shows_the_files_then_writes_them(sessions, tmp_path):  # noqa: F811
    beta = tmp_path / "beta"
    app = CSM(sessions)
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(pilot)
        await pilot.press("T")
        await settle(pilot)
        assert isinstance(app.screen, TeamSetup)
        shown = str(app.screen.query_one("VerticalScroll Static").render())
        assert "coder.md" in shown and "model: sonnet" in shown and crew.SECTION in shown
        assert not (beta / ".claude").exists()  # nothing written yet
        await pilot.press("escape")
        await settle(pilot)
        assert not (beta / ".claude").exists()
        await pilot.press("T", "y")
        await settle(pilot)
        assert sorted(p.name for p in (beta / ".claude" / "agents").iterdir()) == ["coder.md", "scout.md", "tester.md"]
        assert crew.SECTION in (beta / "CLAUDE.md").read_text()
        assert any("Wrote 4 files" in str(n.message) for n in app._notifications)


def test_the_stats_team_report_compares_with_all_opus():
    today = date(2026, 10, 15)
    when = 1792000000.0  # 2026-10-14
    team = {"s1": [agents.Agent("a", "Map it", "code-search", "", model="claude-sonnet-5-5", started=when, weight=100.0),
                   agents.Agent("b", "Do it", "general-purpose", "", model="claude-opus-5-5", started=when, weight=50.0),
                   agents.Agent("c", "Old", "general-purpose", "", model="claude-opus-5-5", started=1.0, weight=999.0)]}
    out = stats.team_section(team, today, per_weight=0.01)
    text = "\n".join(str(x) for x in out if hasattr(x, "plain"))
    # sonnet: $1.00 (all Opus $2.00); opus: $0.50. Paid $1.50, all on Opus $2.50.
    assert "2 agents cost $1.50; the same work all on Opus 5.5 about $2.50 (-40%)" in text
    assert "1 general-purpose agent ran on Opus" in text and "on Sonnet about $0.25" in text
    assert stats.team_section({}, today, 0.01) == []
