import json
import os

from csm.activity import LINE, Activity, wave
from csm.app import CSM
from test_app import sessions, settle  # noqa: F401  (fixture)


def test_wave_fills_width_with_bars():
    w = wave(40, 0.0)
    assert len(w) == 40 and set(w) <= set(LINE) and len(set(w)) > 3
    assert wave(40, 1.0) != w  # it moves


async def test_moves_only_while_a_session_is_working(sessions):  # noqa: F811
    live = sessions.live / "1.json"
    live.write_text(json.dumps({"pid": os.getpid(), "sessionId": "b1", "status": "idle"}))
    app = CSM(sessions)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(pilot)
        bar = app.query_one(Activity)
        assert bar.busy == 0 and set(str(bar.render()).strip()) == {LINE[2]}
        live.write_text(json.dumps({"pid": os.getpid(), "sessionId": "b1", "status": "busy"}))
        app.poll_live()
        await pilot.pause(0.3)
        first = str(bar.render())
        assert bar.busy == 1 and first.startswith("1 working")
        await pilot.pause(0.3)
        assert str(bar.render()) != first  # animating
        live.write_text(json.dumps({"pid": os.getpid(), "sessionId": "b1", "status": "idle"}))
        app.poll_live()
        await pilot.pause(0.3)
        assert bar.busy == 0 and "working" not in str(bar.render())
