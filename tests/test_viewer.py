import pytest
from conftest import assistant, rec, user

from csm import viewer
from csm.app import CSM
from csm.viewer import Transcript, Viewer


@pytest.fixture
def long_session(paths, write, tmp_path):
    d = str(tmp_path / "alpha")
    lines = [rec(type="custom-title", customTitle="Long one")]
    for i in range(40):
        lines.append(user(f"question {i}", d))
        lines.append(assistant([{"type": "tool_use", "name": "Bash", "input": {}},
                                {"type": "tool_use", "name": "Read", "input": {}},
                                {"type": "text", "text": f"answer {i}: **needle** here" if i % 10 == 0 else f"answer {i}"}], d))
    write(d, "l1", *lines)
    return paths


async def settle(pilot):
    for _ in range(2):
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()


async def open_viewer(pilot):
    await settle(pilot)
    await pilot.press("t")
    await settle(pilot)
    return pilot.app.screen


def test_blocks_fold_tool_runs():
    turns = [("user", "hi"), ("tools", "Bash"), ("tools", "Read"), ("tools", "Bash"), ("assistant", "done")]
    assert viewer.blocks(turns) == [("user", "hi"), ("tools", "ran 3 tool calls: Bash, Read"), ("assistant", "done")]
    assert viewer.blocks([("tools", "Bash")]) == [("tools", "ran 1 tool call: Bash")]


def test_find_counts_occurrences_case_insensitively():
    lines = viewer.layout([("assistant", "Needle and needle\n\nnone"), ("user", "NEEDLE")], 60, True)
    assert len(viewer.find(lines, "needle")) == 3
    assert viewer.find(lines, "absent") == []


async def test_shows_every_turn_scrolled_to_the_end(long_session):
    app = CSM(long_session)
    async with app.run_test(size=(100, 30)) as pilot:
        v = await open_viewer(pilot)
        assert isinstance(v, Viewer)
        view = v.query_one(Transcript)
        text = "\n".join(s.text for s in view.lines)
        assert all(f"question {i}" in text for i in range(40))  # the preview stops at 30 messages
        assert "answer 39" in text and "ran 2 tool calls: Bash, Read" in text
        assert view.scroll_y == view.max_scroll_y > 0
        assert "Long one" in str(v.query_one("#head").render())
        await pilot.press("g")
        assert view.scroll_y == 0
        await pilot.press("G")
        assert view.scroll_y == view.max_scroll_y
        await pilot.press("k")
        assert view.scroll_y == view.max_scroll_y - 1


async def test_search_navigation_and_escape(long_session):
    app = CSM(long_session)
    async with app.run_test(size=(100, 30)) as pilot:
        v = await open_viewer(pilot)
        view = v.query_one(Transcript)
        await pilot.press("slash", *"needle", "enter")
        await pilot.pause()
        assert len(view.hits) == 4 and view.pos == 0
        assert "1/4" in str(v.query_one("#count").render())
        first = view.hits[0][0]
        assert view.scroll_y <= first < view.scroll_y + view.size.height
        await pilot.press("n")
        assert view.pos == 1 and "2/4" in str(v.query_one("#count").render())
        await pilot.press("N", "N")
        assert view.pos == 3  # wraps
        await pilot.press("escape")  # clears the search, stays in the viewer
        assert app.screen is v and view.query == "" and not view.hits
        assert str(v.query_one("#count").render()) == ""
        await pilot.press("escape")
        assert app.screen is not v


async def test_escape_closes_the_search_box_first(long_session):
    app = CSM(long_session)
    async with app.run_test(size=(100, 30)) as pilot:
        v = await open_viewer(pilot)
        await pilot.press("slash")
        assert v.query_one("#find").has_class("-on")
        await pilot.press("escape")
        assert app.screen is v and not v.query_one("#find").has_class("-on")
        await pilot.press("slash", *"zzz", "enter")
        assert "no matches" in str(v.query_one("#count").render())


async def test_q_returns_to_the_list(long_session):
    app = CSM(long_session)
    async with app.run_test(size=(100, 30)) as pilot:
        v = await open_viewer(pilot)
        await pilot.press("q")
        await pilot.pause()
        assert app.screen is not v
        assert app.selected().id == "l1"
