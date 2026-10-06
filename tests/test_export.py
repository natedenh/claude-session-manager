from conftest import assistant, rec, user
from test_app import settle

from csm import data, export
from csm.app import CSM


def session(paths, write, tmp_path, sid="s1", title="Fix: the /login bug", *extra):
    cwd = str(tmp_path / "proj")
    write(cwd, sid, user("please fix login", cwd), rec(type="custom-title", customTitle=title),
          assistant("Looking.\n\n## Plan\n- `a`", cwd), *extra)
    return next(s for s in data.load_sessions(paths) if s.id == sid)


def test_render(paths, write, tmp_path):
    cwd = str(tmp_path / "proj")

    def tool(n):
        return assistant([{"type": "tool_use", "name": n, "input": {}}], cwd)

    s = session(paths, write, tmp_path, "s1", "Login",
                tool("Bash"), rec(type="user", message={"role": "user", "content": [{"type": "tool_result"}]}),
                tool("Read"), tool("Bash"), tool("Edit"),
                assistant("Done.", cwd),
                user("thanks", cwd),
                user("secret", cwd, isMeta=True),
                assistant("sidechain", cwd).replace('"type":"assistant"', '"type":"assistant","isSidechain":true'),
                user("<command-name>x</command-name>", cwd),
                rec(type="pr-link", prNumber=3, prUrl="https://x/pull/3"))
    md = export.render(s)
    assert md.startswith('---\ntitle: "Login"\nsession_id: "s1"\n')
    assert 'pr: "https://x/pull/3"' in md and "started:" in md and "last_active:" in md
    assert "## You\n\nplease fix login" in md
    assert "## Claude\n\nLooking.\n\n## Plan\n- `a`" in md
    assert "_Ran 4 tool calls: Bash, Read, Edit_" in md
    assert md.index("_Ran 4") < md.index("Done.") and md.count("## Claude") == 1
    assert "## You\n\nthanks" in md
    assert "secret" not in md and "sidechain" not in md and "command-name" not in md


def test_filename(paths, write, tmp_path):
    s = session(paths, write, tmp_path)
    assert export.filename(s) == "2026-09-30 Fix the login bug.md"
    s.title = "x" * 300
    assert len(export.filename(s)) < 100


def test_collision(paths, write, tmp_path):
    a = session(paths, write, tmp_path, "s1", "Same")
    b = session(paths, write, tmp_path, "s2", "Same")
    first = export.export(a, paths.export)
    assert export.export(a, paths.export) == first
    second = export.export(b, paths.export)
    assert second.name == "2026-09-30 Same (2).md"
    assert export.export(b, paths.export) == second
    assert len(list(paths.export.iterdir())) == 2


def test_export_dir_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CSM_EXPORT_DIR", str(tmp_path / "out"))
    assert data.Paths().export == tmp_path / "out"
    monkeypatch.delenv("CSM_EXPORT_DIR")
    assert data.Paths().export == data.HOME / "Downloads" / "claude-sessions"


async def test_export_key(paths, write, tmp_path, no_real_finder):
    session(paths, write, tmp_path, "s1", "One")
    app = CSM(paths)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("E")
        await settle(pilot)
    [out] = list(paths.export.glob("*.md"))
    assert out.name == "2026-09-30 One.md" and 'session_id: "s1"' in out.read_text()
    assert no_real_finder == [[out]]


async def test_export_marked(paths, write, tmp_path, no_real_finder):
    session(paths, write, tmp_path, "s1", "One")
    session(paths, write, tmp_path, "s2", "Two")
    app = CSM(paths)
    async with app.run_test() as pilot:
        await settle(pilot)
        app.marked = {"s1", "s2"}
        await pilot.press("E")
        await settle(pilot)
    assert sorted(p.name for p in paths.export.glob("*.md")) == ["2026-09-30 One.md", "2026-09-30 Two.md"]
    assert len(no_real_finder) == 1 and len(no_real_finder[0]) == 2
