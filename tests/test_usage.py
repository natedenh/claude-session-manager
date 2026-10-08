import json
import time
from datetime import date

from rich.console import Console

from csm import stats, tips
from csm.usage import FEATURES, Usage, feature_of
from test_app import CSM, settle, sessions  # noqa: F401  (fixture)

DAY = date(2026, 10, 8)


def test_records_features_by_day_and_folds_aliases(tmp_path):
    u = Usage(tmp_path / "u.json", today=DAY)
    assert u.record("app.next_waiting") == "next_waiting" and u.record("next_waiting(True)") == "next_waiting"
    assert u.record("jump(-1)") == "jump(1)" and u.record("cursor_down") is None and u.record("quit") is None
    assert u.total("next_waiting") == 2 and u.last("next_waiting") == "2026-10-08"
    assert feature_of("app.toggle('waiting')") == "toggle('waiting')"


def test_saves_and_drops_old_days(tmp_path):
    p = tmp_path / "u.json"
    p.write_text(json.dumps({"since": "2026-07-01", "days": {"filter": {"2026-07-02": 4, "2026-10-01": 1}}}))
    u = Usage(p, today=DAY)
    u.record("filter")
    u.save(force=True)
    saved = json.loads(p.read_text())
    assert saved["days"]["filter"] == {"2026-10-01": 1, "2026-10-08": 1} and saved["since"] == "2026-07-01"
    assert Usage(p, today=DAY).total("filter") == 2


def test_tips_respect_usage_mute_quiet_and_once_a_day(tmp_path):
    u = Usage(tmp_path / "u.json", today=DAY)
    s = tips.Signals(waiting_opened_by_hand=2)
    tip = tips.pick(u, s, 10_000.0)
    assert tip and tip.id == "tab"
    tips.shown(tip, u, s, 10_000.0)
    assert tips.pick(u, tips.Signals(waiting_opened_by_hand=2, shown_at=s.shown_at), 10_000.0 + 60) is None  # quiet
    assert tips.pick(u, tips.Signals(waiting_opened_by_hand=2), 99_999.0) is None  # already today
    u2 = Usage(tmp_path / "u2.json", today=DAY)
    for _ in range(3):
        u2.record("next_waiting")
    assert tips.pick(u2, tips.Signals(waiting_opened_by_hand=5), 1e6) is None  # you use it already
    u2.muted = True
    assert tips.pick(u2, tips.Signals(opened_full_context=True), 1e6) is None


def plain(group):
    c = Console(width=120, record=True, color_system=None)
    c.print(group)
    return c.export_text()


def test_your_csm_section(tmp_path):
    u = Usage(tmp_path / "u.json", today=DAY)
    assert "Still learning" in plain(stats.Group(*stats.yours(u, DAY)))
    u.since = "2026-09-01"
    u.counts = {"filter": {"2026-10-07": 5}, "summary": {"2026-09-10": 2}}
    out = plain(stats.Group(*stats.yours(u, DAY)))
    assert "Most used, last 7 days" in out and "filter by title" in out
    assert "Not tried yet" in out and "open the session that has waited longest" in out
    assert "Not lately" in out and "2026-09-10" in out
    assert "filter by title" not in out.split("Not tried yet")[1]


async def test_app_counts_keys_and_offers_a_tip(sessions, monkeypatch):  # noqa: F811
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("exclamation_mark", "exclamation_mark")
        assert app.usage.total("toggle('waiting')") == 2
        monkeypatch.setattr(app, "resume_flow", lambda s: None)
        app.waiting["a1"] = time.time()
        app.resume(app.by_id["a1"])
        app.waiting["b1"] = time.time()
        app.resume(app.by_id["b1"])
        assert app.signals.waiting_opened_by_hand == 2 and app.usage.total("open") == 2
        app.poll_live()
        await settle(pilot)
        assert any("tab opens whichever session" in str(n.message) for n in app._notifications)
        await pilot.press("ctrl+t")
        assert app.usage.muted
    assert json.loads(app.usage.path.read_text())["muted"] is True


def test_every_feature_is_bound():
    import re
    from csm import app as appmod
    src = open(appmod.__file__).read()
    bound = set(re.findall(r'Binding\("[^"]+", "(?:app\.)?([^"]+)"', src)) | {"open"}
    assert {f.action for f in FEATURES} <= bound



async def test_toasts_are_plain_text_so_brackets_cant_crash_them(sessions):  # noqa: F811
    """Toasts are parsed when drawn: the "] / [" in a tip once crashed the sidebar."""
    from textual.content import Content
    app = CSM(sessions)
    async with app.run_test(size=(100, 40)) as pilot:
        await settle(pilot)
        app.signals.long_scroll = True
        app.offer_tip()
        app.notify("Sent to “[/] weird [title]”")
        await settle(pilot)
        notes = list(app._notifications)
        assert any("] / [" in n.message for n in notes) and not any(n.markup for n in notes)
        for n in notes:
            Content(n.message) if not n.markup else Content.from_markup(n.message)
