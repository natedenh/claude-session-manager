import json
import os

from csm import data, launch, models
from csm.dialogs import FirstMessage, ModelPick
from test_app import CSM, FakeHost, settle, sessions  # noqa: F401  (fixture)


def test_the_model_goes_before_the_worktree_flag():
    argv = launch.new("/p", worktree=True, prompt="go", model="sonnet").argv
    assert argv[-3:] == ["--model", "sonnet", "-w"]  # -w takes an optional name: nothing may follow it
    assert "--model" not in launch.new("/p").argv  # no pick: Claude Code's own default


def test_choices_cycle():
    seen, m = [], ""
    for _ in range(len(models.CHOICES)):
        m = models.next_choice(m)
        seen.append(m)
    assert seen == ["opus", "sonnet", "haiku", "opusplan", ""]
    assert models.describe("opusplan") == "opusplan (Opus plans, Sonnet writes the code)"
    assert models.describe("") == "Claude Code's default"


async def test_new_sessions_start_on_the_projects_last_pick(sessions, tmp_path):  # noqa: F811
    host = FakeHost()
    app = CSM(sessions, host=host)
    beta = str(tmp_path / "beta")
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("n")
        await settle(pilot)
        assert isinstance(app.screen, FirstMessage) and app.screen.model == ""
        await pilot.press("ctrl+o", "ctrl+o")
        assert "sonnet" in str(app.screen.query_one("#model").render()) and "becomes this project's default" in \
            str(app.screen.query_one("#model").render())
        await pilot.press(*"write the docs", "ctrl+s")
        await settle(pilot)
        [(kind, _, command)] = host.calls
        assert kind == "start" and command.endswith("--model sonnet")
        assert data.State(sessions.state).models == {beta: "sonnet"}
        await pilot.press("n")
        await settle(pilot)
        assert app.screen.model == "sonnet" and "this project's default" in str(app.screen.query_one("#model").render())
        await pilot.press("ctrl+o", "ctrl+o", "ctrl+o")  # haiku, opusplan, back to Claude Code's default
        await pilot.press(*"plain", "ctrl+s")
        await settle(pilot)
        assert "--model" not in host.calls[-1][2] and data.State(sessions.state).models == {}


async def test_m_switches_a_running_session_now_or_when_its_turn_ends(sessions):  # noqa: F811
    host = FakeHost()
    host.panes = {"b1": "%2"}
    app = CSM(sessions, host=host)
    live = sessions.live / "1.json"
    live.write_text(json.dumps({"pid": os.getpid(), "sessionId": "b1", "status": "idle"}))
    async with app.run_test() as pilot:
        await settle(pilot)
        app.poll_host()
        assert app.selected().id == "b1"
        await pilot.press("m")
        await settle(pilot)
        assert isinstance(app.screen, ModelPick)
        await pilot.press("h")
        await settle(pilot)
        assert ("send", "b1", "/model haiku") in host.calls
        live.write_text(json.dumps({"pid": os.getpid(), "sessionId": "b1", "status": "busy"}))
        app.poll_live()
        await settle(pilot)
        await pilot.press("m", "s")
        await settle(pilot)
        assert ("send", "b1", "/model sonnet") not in host.calls  # it's mid-turn: queued instead
        assert app.state.when_idle["b1"] == {"do": "send", "text": "/model sonnet"}
        live.write_text(json.dumps({"pid": os.getpid(), "sessionId": "b1", "status": "idle"}))
        app.poll_live()
        await settle(pilot)
        assert ("send", "b1", "/model sonnet") in host.calls and "b1" not in app.state.when_idle


async def test_m_queues_behind_a_permission_prompt_and_keeps_an_existing_queued_action(sessions):  # noqa: F811
    host = FakeHost()
    host.panes = {"b1": "%2"}
    app = CSM(sessions, host=host)
    (sessions.live / "1.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "b1", "status": "idle"}))
    async with app.run_test() as pilot:
        await settle(pilot)
        app.poll_host()
        assert app.selected().id == "b1"
        app.permission["b1"] = "permission"
        await pilot.press("m", "s")
        await settle(pilot)
        assert ("send", "b1", "/model sonnet") not in host.calls
        assert app.state.when_idle["b1"] == {"do": "send", "text": "/model sonnet"}
        app.state.when_idle["b1"] = {"do": "retire"}
        await pilot.press("m", "h")
        await settle(pilot)
        assert app.state.when_idle["b1"] == {"do": "retire"}
        assert any("@retire" in str(n.message) for n in app._notifications)


async def test_m_needs_a_session_open_in_csm(sessions):  # noqa: F811
    app = CSM(sessions, host=FakeHost())
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("m")
        await settle(pilot)
        assert not isinstance(app.screen, ModelPick)
        assert any("only works for sessions open in csm" in str(n.message) for n in app._notifications)
