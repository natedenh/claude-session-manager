import json

import pytest
from test_app import goto, ids, sessions, settle  # noqa: F401

from csm import data
from csm.app import CSM


def test_tags_and_notes_persist_and_normalize(paths):
    assert data.normalize_tags("#Waiting-On-Chris, #blocked  #blocked #a/b!") == ["waiting-on-chris", "blocked", "ab"]
    st = data.State(paths.state)
    st.set_tags("a", data.normalize_tags("#One #two"))
    st.set_note("a", "  ping chris  ")
    st.set_note("b", "x")
    st.set_note("b", "")
    st.save()
    st2 = data.State(paths.state)
    assert st2.tags == {"a": ["one", "two"]} and st2.notes == {"a": "ping chris"}
    st2.set_tags("a", [])
    assert st2.tags == {}


def test_state_without_tags_or_notes_loads(paths):
    paths.state.write_text(json.dumps({"archived": ["a"], "pinned": ["b"], "flat": True}))
    st = data.State(paths.state)
    assert st.archived == {"a"} and st.tags == {} and st.notes == {}


async def test_tag_edit_shows_in_row_and_preview_and_persists(sessions):
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        goto(app, "a1")
        await pilot.press("number_sign", *"#Waiting-on-chris #Blocked", "enter")
        await settle(pilot)
        assert app.state.tags["a1"] == ["waiting-on-chris", "blocked"]
        assert "#waiting-on-chris #blocked" in app.row(app.by_id["a1"]).plain
        assert any("#waiting-on-chris" in str(r) for r in app.meta(app.by_id["a1"]).renderables)
        assert data.State(sessions.state).tags == {"a1": ["waiting-on-chris", "blocked"]}
        await pilot.press("number_sign")
        assert app.screen.query_one("Input").value == "#waiting-on-chris #blocked"
        await pilot.press("delete", "enter")  # empty clears
        await settle(pilot)
        assert "a1" not in app.state.tags
    assert data.State(sessions.state).tags == {}


async def test_tags_added_to_all_marked(sessions):
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        app.state.set_tags("b1", ["old"])
        await pilot.press("space", "space")  # marks b1 and a2
        await pilot.press("number_sign", *"#new", "enter")
        await settle(pilot)
        assert app.state.tags == {"b1": ["old", "new"], "a2": ["new"]}


async def test_note_edit_remove_preview_and_filter(sessions):
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        goto(app, "a1")
        await pilot.press("i", *"ask about rotation", "enter")
        await settle(pilot)
        assert app.state.notes == {"a1": "ask about rotation"}
        assert any("ask about rotation" in str(r) for r in app.meta(app.by_id["a1"]).renderables)
        await pilot.press("slash", *"rotation")
        assert ids(app) == ["s:a1"]
        await pilot.press("escape")
        goto(app, "a1")
        await pilot.press("i", "delete", "enter")
        await settle(pilot)
        assert app.state.notes == {}
    assert data.State(sessions.state).notes == {}


@pytest.mark.parametrize("flat", [False, True])
async def test_hash_filter_matches_tag_prefix(sessions, flat):
    st = data.State(sessions.state)
    st.flat = flat
    st.set_tags("a1", ["waiting-on-chris"])
    st.set_tags("b1", ["later"])
    st.set_note("a2", "waiting for review")
    st.save()
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("slash", *"#wait")
        assert ids(app) == ["s:a1"]  # the note on a2 is not a tag
        await pilot.press("escape", "slash", *"waiting")
        assert ids(app) == ["s:a2"]  # plain tokens search notes, not tags
        await pilot.press("escape", "slash", *"#later beta")
        assert ids(app) == ["s:b1"]
