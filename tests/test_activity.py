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


def test_glow_has_a_bright_head_and_fading_tail():
    from csm.activity import TAIL, glow
    g = glow(60, 3.0, 1)
    head = g.index(max(g))
    assert g[head] == 1.0 and g[head - 1] < 1.0 and g[head - TAIL + 1] < g[head - 1]
    assert sum(1 for x in glow(120, 3.0, 2) if x == 1.0) == 2  # two sparks


async def test_wave_eases_out_to_a_flat_line_and_stops(sessions):  # noqa: F811
    live = sessions.live / "1.json"
    live.write_text(json.dumps({"pid": os.getpid(), "sessionId": "b1", "status": "busy"}))
    app = CSM(sessions)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(pilot)
        bar = app.query_one(Activity)
        await pilot.pause(1.0)
        assert bar.amp > 0.5
        live.write_text(json.dumps({"pid": os.getpid(), "sessionId": "b1", "status": "idle"}))
        app.poll_live()
        await pilot.pause(0.2)
        assert 0 < bar.amp < 1 and bar.timer._active.is_set()  # still settling
        await pilot.pause(2.5)
        assert bar.amp == 0.0 and not bar.timer._active.is_set()
        assert set(str(bar.render()).strip()) == {LINE[2]}


async def test_warn_color_is_readable_on_light_themes(sessions, monkeypatch):  # noqa: F811
    from csm.app import WARN_ON_LIGHT
    for theme, expected in (("ansi-light", WARN_ON_LIGHT), ("ansi-dark", "yellow")):
        app = CSM(sessions, theme=theme)
        async with app.run_test() as pilot:
            await settle(pilot)
            assert str(app.console.get_style("warn").color.name).lower() == expected.lower()


async def test_working_dots_pulse_and_idle_dots_dont(sessions):  # noqa: F811
    from csm.app import SessionList
    (sessions.live / "1.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "b1", "status": "busy"}))
    (sessions.live / "2.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "a2", "status": "idle"}))
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        lst = app.query_one(SessionList)
        dot = lambda sid: lst.get_option(f"s:{sid}").prompt.spans[0].style
        assert app.pulse_timer._active.is_set()
        seen_b1, seen_a2 = {str(dot("b1"))}, {str(dot("a2"))}
        for _ in range(3):
            app.pulse()
            seen_b1.add(str(dot("b1")))
            seen_a2.add(str(dot("a2")))
        assert seen_b1 == {"warn", "warn.dim"} and seen_a2 == {"green"}


def test_every_style_fills_the_width_and_moves():
    from csm.activity import STYLES, Activity
    bar = Activity()
    bar.amp, bar.busy = 1.0, 2
    for kind in STYLES:
        bar.kind = kind
        bar.phase = 0.0
        line, light = bar.frame(50)
        assert len(line) == 50 and len(light) == 50, kind
        bar.phase = 2.0
        assert bar.frame(50)[0] != line, kind


def test_strands_draws_one_sine_per_session():
    from csm.activity import strands
    one, three = strands(30, 1.0, 1.0, 1), strands(30, 1.0, 1.0, 3)
    dots = lambda s: sum(bin(ord(c) - 0x2800).count("1") for c in s)
    assert all(0x2800 <= ord(c) <= 0x28FF for c in one + three) and dots(three) > dots(one)


async def test_tilde_cycles_the_style_saves_it_and_plays_it(sessions):  # noqa: F811
    from csm import data
    from csm.activity import STYLES
    app = CSM(sessions)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(pilot)
        bar = app.query_one(Activity)
        assert bar.kind == "wave" and not bar.active
        await pilot.press("~")
        await pilot.pause(0.3)
        assert bar.kind == STYLES[1] and bar.active and str(bar.render()).startswith(f"0 working · {STYLES[1]}")
        assert data.State(sessions.state).wave == STYLES[1]
        await pilot.pause(5.5)  # the demo, then easing back out
        assert not bar.active and bar.amp == 0.0  # back to the flat line
        await pilot.click("#activity")
        assert bar.kind == STYLES[2]
    app = CSM(sessions)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(pilot)
        assert app.query_one(Activity).kind == STYLES[2]  # remembered
