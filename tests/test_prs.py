import json
import subprocess

import pytest

from csm import prs
from csm.prs import fetch as real_fetch  # the autouse fixture stubs prs.fetch
from csm.app import CSM, pr_style, pr_summary
from test_app import sessions, settle  # noqa: F401

URL = "u"  # the PR url of session a2 in the sessions fixture


def run(name, status="COMPLETED", conclusion="SUCCESS"):
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion}


def ctx(state):
    return {"__typename": "StatusContext", "context": "ci", "state": state}


def payload(checks=(), state="OPEN", draft=False, review="", merged=None):
    return {"state": state, "isDraft": draft, "reviewDecision": review, "statusCheckRollup": list(checks),
            "mergedAt": merged}


def test_parse_passing():
    st = prs.parse(payload([run("a"), run("b", conclusion="SKIPPED"), ctx("SUCCESS")], review="APPROVED"))
    assert (st.state, st.checks, st.review, st.passed, st.total) == ("open", "passing", "approved", 3, 3)


def test_parse_failing_beats_pending():
    st = prs.parse(payload([run("a"), run("b", conclusion="FAILURE"), run("c", "IN_PROGRESS", "")]))
    assert (st.checks, st.passed, st.total) == ("failing", 1, 3)


def test_parse_pending_and_status_context():
    assert prs.parse(payload([run("a"), run("b", "QUEUED", "")])).checks == "pending"
    assert prs.parse(payload([ctx("PENDING")])).checks == "pending"
    assert prs.parse(payload([ctx("ERROR")])).checks == "failing"


def test_parse_merged_draft_closed_none():
    merged = prs.parse(payload([run("a")], state="MERGED", merged="2026-01-01T00:00:00Z"))
    assert merged.state == "merged" and merged.final
    assert prs.parse(payload(draft=True)).state == "draft"
    assert prs.parse(payload(state="CLOSED")).state == "closed"
    none = prs.parse(payload())
    assert (none.checks, none.review, none.total) == ("none", None, 0)
    assert prs.parse(payload(review="CHANGES_REQUESTED")).review == "changes_requested"


def test_freshness():
    assert not prs.PRStatus("open", "none", None, 0, 0, 1000).stale(1000 + 299)
    assert prs.PRStatus("open", "none", None, 0, 0, 1000).stale(1000 + 301)
    assert not prs.PRStatus("merged", "none", None, 0, 0, 1000).stale(1000 + 3600)
    assert prs.PRStatus("closed", "none", None, 0, 0, 1000).stale(1000 + 90000)


def test_refresh_caches_and_skips_fresh(paths, monkeypatch):
    calls = []
    monkeypatch.setattr(prs, "fetch", lambda url: calls.append(url) or prs.parse(payload([run("a")])))
    assert prs.refresh(paths, ["x", "y"])
    assert calls == ["x", "y"] and prs.cached(paths, "x").checks == "passing"
    assert not prs.refresh(paths, ["x", "y"])
    assert calls == ["x", "y"]
    assert prs.cached(paths, "nope") is None


def test_refresh_failure_records_nothing(paths, monkeypatch):
    monkeypatch.setattr(prs, "fetch", lambda url: None)
    assert not prs.refresh(paths, ["x"])
    assert not paths.prs.exists()


def test_corrupt_cache(paths):
    paths.prs.write_text("{nope")
    assert prs.load(paths) == {}


def test_fetch_failures(monkeypatch):
    def missing(*a, **k):
        raise FileNotFoundError("gh")
    monkeypatch.setattr(subprocess, "run", missing)
    assert real_fetch("u") is None
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "auth"))
    assert real_fetch("u") is None
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "garbage", ""))
    assert real_fetch("u") is None
    out = json.dumps(payload([run("a")]))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""))
    assert real_fetch("u").checks == "passing"


@pytest.mark.parametrize("kw,style", [
    (dict(), "green"),
    (dict(checks=[run("a", "IN_PROGRESS", "")]), "warn"),
    (dict(review="REVIEW_REQUIRED"), "warn"),
    (dict(checks=[run("a", conclusion="FAILURE")]), "red"),
    (dict(review="CHANGES_REQUESTED", checks=[run("a")]), "red"),
    (dict(state="MERGED"), "magenta"),
    (dict(state="CLOSED"), "dim"),
    (dict(draft=True), "dim"),
])
def test_pr_style(kw, style):
    assert pr_style(prs.parse(payload(**kw))) == style


def test_pr_style_unknown_is_green():
    assert pr_style(None) == "green"


def test_pr_summary():
    st = prs.parse(payload([run("a")] * 12, review="APPROVED"))
    assert pr_summary(st) == "open · checks 12/12 passing · approved"
    assert pr_summary(prs.parse(payload(state="MERGED"))) == "merged"


async def test_app_colors_icon_and_preview(sessions, monkeypatch):  # noqa: F811
    monkeypatch.setattr(prs, "fetch", lambda url: prs.parse(payload([run("a"), ctx("PENDING")], review="APPROVED")))
    app = CSM(sessions)
    async with app.run_test() as pilot:
        await settle(pilot)
        await settle(pilot)
        s = app.by_id["a2"]
        assert app.pr_status[URL].checks == "pending"
        assert any(sp.style == "warn" for sp in app.row(s).spans)
        text = "\n".join(r.plain for r in app.meta(s).renderables if hasattr(r, "plain"))
        assert "open · checks 1/2 pending · approved" in text
