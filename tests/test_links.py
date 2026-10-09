import os

from conftest import rec, user

from csm import links
from csm.app import CSM
from test_app import desktop_record, sessions, settle  # noqa: F401


def test_editor_fallback_order(tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    monkeypatch.setenv("PATH", str(bin_))
    monkeypatch.delenv("CSM_EDITOR", raising=False)
    assert links.editor_argv("/p") == [links.OPENER, "/p"]
    (bin_ / "cursor").write_text("")
    (bin_ / "cursor").chmod(0o755)
    assert links.editor_argv("/p") == ["cursor", "/p"]
    (bin_ / "code").write_text("")
    (bin_ / "code").chmod(0o755)
    assert links.editor_argv("/p") == ["code", "/p"]
    monkeypatch.setenv("CSM_EDITOR", "zed --new")
    assert links.editor_argv("/p") == ["zed", "--new", "/p"]


async def test_g_opens_pr_or_notifies(paths, write, tmp_path, launched):
    d = str(tmp_path / "proj")
    write(d, "with", user("hi", d), rec(type="pr-link", prNumber=7, prUrl="https://example.com/pr/7"))
    write(d, "without", user("hi", d))
    os.utime(paths.projects / next(iter(os.listdir(paths.projects))) / "with.jsonl", (1, 1))
    app = CSM(paths)
    async with app.run_test() as pilot:
        await settle(pilot)
        assert app.selected().id == "without"
        await pilot.press("g")
        assert launched == []
        await pilot.press("j", "g")
        assert app.selected().id == "with"
    assert launched == [[links.OPENER, "https://example.com/pr/7"]]


async def test_dot_opens_project_dir(paths, write, tmp_path, launched, monkeypatch):
    d = tmp_path / "proj"
    d.mkdir()
    write(str(d), "s1", user("hi", str(d)))
    monkeypatch.setenv("CSM_EDITOR", "myedit")
    app = CSM(paths)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("full_stop")
        await pilot.press("k")  # project header
        await pilot.press("full_stop")
    assert launched == [["myedit", str(d)]] * 2


async def test_dot_notifies_when_dir_is_gone(paths, write, tmp_path, launched):
    d = str(tmp_path / "gone")
    write(d, "s1", user("hi", d))
    os.rmdir(d)
    app = CSM(paths)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("full_stop")
    assert launched == []


async def test_D_opens_desktop_record(sessions, launched):
    desktop_record(sessions, "b1", False, local_id="local_abc-123")
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("D")
    assert launched == [[links.OPENER, "claude://code/continue?session=local_abc-123"]]


async def test_D_rejects_unknown_and_malformed(sessions, launched):
    desktop_record(sessions, "b1", False, local_id="local_x&evil=1")
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("D")  # malformed
        await pilot.press("j", "D")  # a2: desktop doesn't know it
    assert launched == []
