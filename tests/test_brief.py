from types import SimpleNamespace

import pytest

from csm import brief, launch
from test_app import FakeHost, SessionList, sessions, settle  # noqa: F401  (fixture)


class Client:
    def __init__(self, text="Goal: ship it.", stop="end_turn"):
        self.calls, self.text, self.stop = [], text, stop
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        blocks = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=self.text)]
        return SimpleNamespace(content=blocks, stop_reason=self.stop)


def test_model_follows_claude_codes_provider():
    assert brief.model_for({}) == "claude-opus-5-5"
    assert brief.model_for({"CLAUDE_CODE_USE_BEDROCK": "1", "ANTHROPIC_DEFAULT_OPUS_MODEL": "us.x"}) == "us.x"
    assert brief.model_for({"CLAUDE_CODE_USE_BEDROCK": "1"}) == "us.anthropic.claude-opus-5-5"
    assert brief.model_for({"CSM_BRIEF_MODEL": "m", "CLAUDE_CODE_USE_BEDROCK": "1"}) == "m"


def test_write_sends_the_transcript_and_points_back_at_it(sessions):  # noqa: F811
    from csm import data
    s = next(x for x in data.load_sessions(sessions) if x.id == "a1")
    c = Client()
    text = brief.write(s, c, "m")
    assert text.startswith("Goal: ship it.") and text.endswith(f"transcript is at {s.path} if you need more detail.")
    [call] = c.calls
    assert call["model"] == "m" and "Fix login bug" in call["messages"][0]["content"]
    with pytest.raises(RuntimeError):
        brief.write(s, Client(stop="refusal"), "m")
    with pytest.raises(RuntimeError):
        brief.write(s, Client(text=""), "m")


def test_new_can_be_named():
    assert launch.new("/p", name="X (continued)").argv[-2:] == ["--name", "X (continued)"]


async def test_B_offers_the_brief_to_edit_then_starts_a_named_session(sessions, monkeypatch):  # noqa: F811
    monkeypatch.setattr(brief, "write", lambda s: f"Carry on with {s.title}.")
    host = FakeHost()
    from csm.app import CSM, FirstMessage
    app = CSM(sessions, host=host)
    async with app.run_test() as pilot:
        await settle(pilot)
        s = app.selected()
        await pilot.press("B")
        await app.workers.wait_for_complete()
        await settle(pilot)
        assert isinstance(app.screen, FirstMessage)
        assert app.screen.query_one("TextArea").text == f"Carry on with {s.title}."
        await pilot.press("ctrl+s")
        await settle(pilot)
        [(kind, sid, command)] = host.calls
        assert kind == "start" and f"--name '{s.title} (continued)'" in command and host.cwds[sid] == s.cwd
