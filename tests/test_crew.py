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
    assert [st.path.name for st in written] == ["scout.md", "tester.md", "CLAUDE.md"]
    assert (tmp_path / ".claude" / "agents" / "coder.md").read_text() == "mine"
    scout = (tmp_path / ".claude" / "agents" / "scout.md").read_text()
    assert scout.startswith("---\nname: scout\n") and "\nmodel: haiku\n" in scout
    md = (tmp_path / "CLAUDE.md").read_text()
    assert md.startswith("# Project\nNotes.\n") and md.count(crew.SECTION) == 1
    assert [st.action for st in crew.plan(str(tmp_path))] == ["skip"] * 4  # run again: nothing to do
    assert crew.apply(crew.plan(str(tmp_path))) == []


FIRST_CLAUDE_MD = """
## Delegating to subagents

Plan and review the work yourself; hand the doing to this project's subagents, which run on cheaper
models (they're in .claude/agents):

- **scout** (Haiku): finding where something is, what calls what, how the repo does something.
- **coder** (Sonnet): a change you can specify: the files or area, the behavior, how to check it.
  Run independent pieces in parallel.
- **tester** (Haiku): running the tests, linter and type checker and reporting failures.

Keep for yourself: design decisions, bugs whose cause is unknown, and anything about auth, secrets,
pricing or production config. Review what the agents report before calling the work done. When you use
an agent that isn't pinned, such as general-purpose, pass `model` explicitly: sonnet unless the task
needs Opus.
"""
PINNED = {"scout": "546618c31efd8858", "coder": "96fb3f5680542a28", "tester": "d3c536b45105b641",
          "CLAUDE.md": "10f7cd688309588f"}
OLD_TESTER = crew.AGENTS["tester"].replace("tools: Read, Grep, Glob, Bash\n", "")


def _agents(tmp_path):
    d = tmp_path / ".claude" / "agents"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_an_unchanged_earlier_tester_is_updated(tmp_path):
    assert crew.digest(OLD_TESTER) == "788c6f2fa4acc2be"
    path = _agents(tmp_path) / "tester.md"
    path.write_text(OLD_TESTER)
    step = next(st for st in crew.plan(str(tmp_path)) if st.path == path)
    assert step.action == "update" and step.old == OLD_TESTER
    done = crew.apply([step])
    assert [st.action for st in done] == ["update"]
    assert path.read_text() == crew.AGENTS["tester"].lstrip("\n")


def test_a_hand_edited_tester_is_left_alone(tmp_path):
    path = _agents(tmp_path) / "tester.md"
    path.write_text(OLD_TESTER + "my own rule\n")
    step = next(st for st in crew.plan(str(tmp_path)) if st.path == path)
    assert step.action == "skip" and step.old == path.read_text() and "by hand" in step.note
    assert crew.apply([step]) == []
    assert path.read_text() == OLD_TESTER + "my own rule\n"


def test_an_earlier_claude_md_section_is_replaced_in_place(tmp_path):
    assert crew.digest(FIRST_CLAUDE_MD) == "2b3838e269801421"
    md = tmp_path / "CLAUDE.md"
    md.write_text("# P\n\nintro\n" + FIRST_CLAUDE_MD.strip() + "\n## Later\n\nkeep me\n")
    step = next(st for st in crew.plan(str(tmp_path)) if st.path == md)
    assert step.action == "update"
    assert [st.action for st in crew.apply([step])] == ["update"]
    text = md.read_text()
    assert text.startswith("# P\n\nintro\n") and text.count(crew.CLAUDE_MD.strip()) == 1
    assert text.endswith("\n## Later\n\nkeep me\n")
    assert "finding where something is" not in text


def test_a_hand_edited_section_is_left_alone(tmp_path):
    md = tmp_path / "CLAUDE.md"
    md.write_text("# P\n" + crew.SECTION + "\n\nmy own words\n")
    step = next(st for st in crew.plan(str(tmp_path)) if st.path == md)
    assert step.action == "skip" and "by hand" in step.note and "my own words" in step.old


def test_an_update_is_skipped_if_the_file_changed_since_the_plan(tmp_path):
    path = _agents(tmp_path) / "tester.md"
    path.write_text(OLD_TESTER)
    step = next(st for st in crew.plan(str(tmp_path)) if st.path == path)
    path.write_text("changed meanwhile")
    assert crew.apply([step]) == []
    assert path.read_text() == "changed meanwhile"


def test_an_unreadable_claude_md_is_never_touched(tmp_path):
    md = tmp_path / "CLAUDE.md"
    md.write_bytes(b"\xff\xfe bad")
    steps = crew.plan(str(tmp_path))
    step = next(st for st in steps if st.path == md)
    assert step.action == "skip" and "couldn't read" in step.note
    crew.apply(steps)
    assert md.read_bytes() == b"\xff\xfe bad"
    assert crew.apply([crew.Step(md, crew.CLAUDE_MD, "append")]) == []
    assert md.read_bytes() == b"\xff\xfe bad"


def test_planning_right_after_applying_is_all_up_to_date(tmp_path):
    _agents(tmp_path)
    (_agents(tmp_path) / "tester.md").write_text(OLD_TESTER)
    (tmp_path / "CLAUDE.md").write_text(FIRST_CLAUDE_MD)
    crew.apply(crew.plan(str(tmp_path)))
    steps = crew.plan(str(tmp_path))
    assert [(st.action, st.note) for st in steps] == [("skip", "up to date")] * 4


def test_the_current_digests_are_pinned():
    now = {name: crew.digest(t) for name, t in crew.AGENTS.items()} | {"CLAUDE.md": crew.digest(crew.CLAUDE_MD)}
    assert now == PINNED, ("you changed a team file's text; add its previous digest to crew.EARLIER, "
                           "then update these values")


def test_no_earlier_digest_is_a_current_one():
    for name, old in crew.EARLIER.items():
        current = crew.digest(crew.CLAUDE_MD if name == "CLAUDE.md" else crew.AGENTS[name])
        assert current not in old


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
