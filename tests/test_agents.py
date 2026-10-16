import json
import os
import time

import pytest
from conftest import rec

from csm import agents
from csm.viewer import Viewer
from test_app import CSM, SessionList, settle, sessions  # noqa: F401  (fixture)


def agent_file(main, aid, task, model, kind="general-purpose", started="2026-10-10T15:00:00Z",
               ended="2026-10-10T15:04:00Z", said="All done: 3 files changed.", handback=None):
    sub = main.parent / main.stem / "subagents"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / f"agent-{aid}.meta.json").write_text(json.dumps({"agentType": kind, "description": task, "model": "sonnet"}))
    lines = [rec(type="user", isSidechain=True, agentId=aid, timestamp=started, message={"role": "user", "content": task}),
             rec(type="assistant", isSidechain=True, agentId=aid, timestamp=ended, message={
                 "model": model, "id": f"m-{aid}", "content": [{"type": "text", "text": said}],
                 "usage": {"input_tokens": 0, "output_tokens": 100}})]
    if handback:
        lines.append(rec(type="assistant", isSidechain=True, agentId=aid, timestamp=ended, message={
            "model": model, "id": f"h-{aid}", "content": [{"type": "tool_use", "name": "SubagentHandback",
                                                          "input": {"message": handback}}]}))
    (sub / f"agent-{aid}.jsonl").write_text("".join(l + "\n" for l in lines))
    return sub / f"agent-{aid}.jsonl"


def finished(aid, result):
    return rec(type="queue-operation", operation="enqueue", content=(
        f"<task-notification>\n<task-id>{aid}</task-id>\n<status>completed</status>\n"
        f"<summary>Agent finished</summary>\n<result>{result}</result>\n</task-notification>"))


@pytest.fixture
def team(sessions):  # noqa: F811
    main = next(sessions.projects.glob("*/a1.jsonl"))
    agent_file(main, "x1", "Map the [checkout] code", "claude-sonnet-5-5", kind="code-search")
    running = agent_file(main, "x2", "Run the tests", "claude-haiku-5-5", said="Running them now.")
    os.utime(running, (time.time(),) * 2)
    with open(main, "a") as f:
        f.write(finished("x1", "## Found it\\n**charges.py** creates the charge.") + "\n")
    (sessions.live / "1.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "a1", "status": "busy"}))
    return sessions, main


def test_reading_a_sessions_agents(team):
    _, main = team
    done, running = agents.read(main, live=True)
    assert (done.task, done.type, done.model_label, done.status) == ("Map the [checkout] code", "code-search", "sonnet", "done")
    assert done.result == "Found it charges.py creates the charge." and round(done.took) == 240
    assert (running.status, running.model_label, running.result) == ("running", "haiku", "Running them now.")
    assert [a.status for a in agents.read(main, live=False)] == ["done", "stopped"]  # the session isn't running


def test_a_handback_is_the_result(sessions):  # noqa: F811
    main = next(sessions.projects.glob("*/a1.jsonl"))
    agent_file(main, "h1", "Wave 1", "claude-sonnet-5-5", said="Now append tests.", handback="Branch feat/x, 45 passed.")
    with open(main, "a") as f:
        f.write(finished("h1", "This agent's report was delivered to you as a message.") + "\n")
    [a] = agents.read(main, live=False)
    assert a.result == "Branch feat/x, 45 passed."


async def test_team_rows_preview_and_viewer(team, sent):
    paths, main = team
    app = CSM(paths)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot)
        app.refresh_agents()
        await settle(pilot)
        lst = app.query_one(SessionList)
        ids = [o.id for o in lst.options]  # with the blank spacer rows, so positions match the list
        at = ids.index("s:a1")
        assert ids[at + 1:at + 3] == ["a:a1:x2", "A:a1"]  # the running one shows; the finished one is folded
        assert "● haiku general-purpose · Run the tests" in lst.get_option("a:a1:x2").prompt.plain
        assert "▸ 1 agent 1 sonnet" in lst.get_option("A:a1").prompt.plain
        assert "1 agent working" in str(app.query_one("#status").render())
        assert "Team 2 agents" in "".join(str(r) for r in app.meta(app.by_id["a1"]).renderables)
        lst.highlighted = ids.index("A:a1")
        await pilot.press("enter")  # unfold
        await settle(pilot)
        assert "a:a1:x1" in [o.id for o in lst.options]
        lst.highlighted = [o.id for o in lst.options].index("a:a1:x1")
        await settle(pilot)
        assert app.selected().id == "a1" and app.preview_key == "x1"  # session keys act on its session
        s, a = app.selected_agent()
        shown = "".join(str(r) for r in app.agent_meta(s, a).renderables)
        assert "Found it charges.py" in shown and "code-search · sonnet 5.5" in shown and "agent of “Fix login bug”" in shown
        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(app.screen, Viewer) and app.screen.session.path.endswith("agent-x1.jsonl")
        await pilot.press("escape")
        # The running agent finishes: a notification, once.
        with open(main, "a") as f:
            f.write(finished("x2", "12 passed.") + "\n")
        app.refresh_agents()
        await settle(pilot)
        assert any("agent “Run the tests” finished" in str(n.message) for n in app._notifications)
        assert sent == ["Fix login bug: agent “Run the tests” finished"]
