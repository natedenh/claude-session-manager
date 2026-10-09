"""Regenerate docs/screenshots/*.svg from made-up sessions (never your real ones).

    uv run python scripts/screenshots.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
sys.path.insert(0, str(ROOT / "src"))
# A made-up home, set before csm reads HOME, so paths show as ~/code/... rather than a temp dir.
HOME = Path(tempfile.mkdtemp(prefix="csm-demo-")).resolve()  # resolved: Claude Code trusts folders by real path
os.environ["HOME"] = str(HOME)

from csm import data, prs, summary  # noqa: E402

NOW = time.time()

# (project, title, minutes ago it last wrote, live status or None, PR (number, state, checks) or None)
SESSIONS = [
    ("acme-web", "Fix the checkout race when two tabs pay at once", 2, "busy", (482, "open", "pending")),
    ("acme-web", "Add Apple Pay to the payment sheet", 14, "idle", (479, "open", "passing")),
    ("acme-web", "Dark mode for the settings pages", 95, None, (471, "merged", "passing")),
    ("acme-web", "Why is the bundle 40% bigger since Monday?", 300, None, None),
    ("billing-api", "Retry webhooks with backoff and a dead-letter queue", 6, "busy", (1203, "open", "failing")),
    ("billing-api", "Invoice PDFs: switch to the new template", 48, "idle", None),
    ("billing-api", "Migrate cents columns to bigint", 1440 * 2, None, (1188, "merged", "passing")),
    ("infra", "Terraform the staging Redis cluster", 33, "idle", None),
    ("infra", "Rotate the CI deploy keys", 1440 * 3, None, None),
    ("docs-site", "Write the getting-started tutorial", 180, None, (88, "open", "passing")),
]
# ("user", text), ("assistant", text) or ("tool", name, input, result): a tool call and what it returned.
TURNS = {
    0: [("user", "Two tabs can both pay for the same cart. Find out why and fix it."),
        ("tool", "Grep", {"pattern": "create_charge", "path": "app"},
         "app/payments/checkout.py:88:    charge = create_charge(cart)\napp/payments/charges.py:14:def create_charge(cart):"),
        ("tool", "Read", {"file_path": "app/payments/checkout.py", "offset": 80, "limit": 15},
         "80  def pay(cart_id):\n81      cart = Cart.get(cart_id)\n82      if cart.paid:\n83          return cart.charge\n"
         "...\n88      charge = create_charge(cart)\n89      cart.mark_paid(charge)"),
        ("assistant", "Found it. Both tabs read the cart before either marks it paid, so each one gets past the "
                      "`cart.paid` check and **creates its own charge**.\n\nI'll make the charge idempotent on "
                      "`cart_id`:\n\n1. A unique index on `charges(cart_id)`\n2. On a conflict, return the charge "
                      "that won\n3. A test that pays from two tasks at once"),
        ("user", "Go ahead, and open a PR when the test passes."),
        ("tool", "Edit", {"file_path": "app/payments/charges.py", "old_string": "    return Charge.create(cart=cart)",
                          "new_string": "    try:\n        return Charge.create(cart=cart)\n    except UniqueViolation:\n"
                                        "        return Charge.get(cart_id=cart.id)"},
         "The file app/payments/charges.py has been updated."),
        ("tool", "Bash", {"command": "pytest tests/payments -q", "description": "Run the payment tests"},
         "..........\n10 passed in 1.84s"),
        ("tool", "Bash", {"command": "gh pr create --fill", "description": "Open the PR"},
         "https://github.com/acme/acme-web/pull/482"),
        ("assistant", "Opened **#482**. Two tabs paying at once now get the same charge back, and the new test "
                      "`test_two_tabs_pay_once` covers it. CI is running.")],
}
DIGESTS = {
    1: ("needs_input", "Should Apple Pay replace the saved-card button, or sit next to it?", [], []),
    4: ("needs_input", "The dead-letter queue needs an SQS policy change. OK to apply it to staging?", [],
        ["retry up to 8 times with jittered backoff"]),
    2: ("done", None, ["merged PR #471, dark mode for settings"], []),
    6: ("done", None, ["merged PR #1188; amounts are bigint everywhere"], ["keep the old columns for a week"]),
    7: ("idle", None, ["planned the staging Redis module"], []),
}


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def build(home: Path) -> data.Paths:
    paths = data.Paths(claude=home / "claude", cache=home / "cache.json", prs=home / "prs.json",
                       state=home / "state" / "state.json", status=home / "state" / "status",
                       trash=home / "Trash", export=home / "export", desktop=home / "desktop",
                       summaries=home / "summaries.json")
    paths.projects.mkdir(parents=True)
    paths.live.mkdir()
    statuses, digests, live_n = {}, summary.Digests(paths.summaries), 0
    for i, (project, title, ago_min, live, pr) in enumerate(SESSIONS):
        cwd = str(HOME / "code" / project)
        os.makedirs(cwd, exist_ok=True)
        sid = f"{0x3f2a9c10 + i * 7919:08x}-5d1e-4c2b-9a7f-{0x1b2c3d4e5f60 + i * 104729:012x}"
        last = NOW - ago_min * 60
        lines = []
        # A week of activity, so the sparklines and stats have something to show. Not in the featured
        # session, which Claude Code itself shows in the tmux screenshot: there it would be empty turns.
        days = 1 + (i * 3) % 7
        for d in (range(days, -1, -1) if i else []):
            for m in range(0, 20 + (i * 7 + d * 11) % 70, 3):
                ts = last - d * 86400 - m * 60
                lines.append({"type": "assistant", "timestamp": iso(ts), "cwd": cwd,
                              "message": {"id": f"msg_{i}_{d}_{m}", "role": "assistant", "content": [],
                                          "model": "claude-sonnet-5-5" if i % 3 == 1 else "claude-opus-5-5",
                                          "usage": {"input_tokens": 400, "cache_creation_input_tokens": 3000,
                                                    "cache_read_input_tokens": 40000, "output_tokens": 900}}})
        turns = TURNS.get(i, [("user", title), ("assistant", "On it. I'll start by reading the code involved.")])
        usage = {"input_tokens": 400, "cache_creation_input_tokens": 3000, "cache_read_input_tokens": 40000,
                 "output_tokens": 900}
        for n, turn in enumerate(turns):
            ts = iso(last - (len(turns) - n) * 20)
            if turn[0] == "user":
                lines.append({"type": "user", "timestamp": ts, "cwd": cwd, "message": {"role": "user", "content": turn[1]}})
            elif turn[0] == "assistant":
                lines.append({"type": "assistant", "timestamp": ts, "cwd": cwd, "message": {
                    "id": f"msg_{i}_t{n}", "model": "claude-opus-5-5", "role": "assistant", "type": "message",
                    "content": [{"type": "text", "text": turn[1]}], "stop_reason": "end_turn", "usage": usage}})
            else:
                _, name, args, result = turn
                tid = f"toolu_{i}_{n:02d}"
                lines.append({"type": "assistant", "timestamp": ts, "cwd": cwd, "message": {
                    "id": f"msg_{i}_t{n}", "model": "claude-opus-5-5", "role": "assistant", "type": "message",
                    "content": [{"type": "tool_use", "id": tid, "name": name, "input": args}],
                    "stop_reason": "tool_use", "usage": usage}})
                lines.append({"type": "user", "timestamp": ts, "cwd": cwd, "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": tid, "content": result}]}})
        # What Claude Code's resume needs on every record: ids chaining each to the one before.
        prev = None
        for n, rec in enumerate(lines):
            uid = f"{i:04x}{n:04x}-0000-4000-8000-{n:012x}"
            rec.update({"uuid": uid, "parentUuid": prev, "sessionId": sid, "isSidechain": False, "userType": "external",
                        "entrypoint": "cli", "version": "2.1.295", "gitBranch": "main"})
            prev = uid
        lines.insert(0, {"type": "custom-title", "customTitle": title, "sessionId": sid})
        lines.append({"type": "cost-state", "totalCostUSD": round(1.5 + i * 2.37, 2), "totalLinesAdded": 120 + i * 85})
        if pr:
            url = f"https://github.com/acme/{project}/pull/{pr[0]}"
            lines.append({"type": "pr-link", "prNumber": pr[0], "prUrl": url})
            statuses[url] = prs.PRStatus(state=pr[1], checks=pr[2], review="approved" if pr[2] == "passing" else None,
                                         passed=12 if pr[2] == "passing" else 9, total=12, fetched_at=NOW)
        d = paths.projects / data.encode_dir(cwd)
        d.mkdir(exist_ok=True)
        f = d / f"{sid}.jsonl"
        # Compact, as Claude Code writes it: csm's fast scan looks for e.g. "cwd":" without a space.
        f.write_text("".join(json.dumps(x, separators=(",", ":")) + "\n" for x in lines))
        os.utime(f, (last, last))
        if live:
            (paths.live / f"{live_n}.json").write_text(json.dumps(
                {"pid": os.getpid(), "sessionId": sid, "status": live, "kind": "interactive"}))
            live_n += 1
    paths.prs.write_text(json.dumps({u: asdict(s) for u, s in statuses.items()}))
    for s in data.load_sessions(paths):
        i = next(n for n, row in enumerate(SESSIONS) if row[1] == s.title)
        status, q, done, decided = DIGESTS.get(i, ("in_progress", None, [], []))
        digests.put(s, summary.Digest(status=status, question=q, done=done, decisions=decided, headline=s.title))
    digests.save()
    return paths


async def shoot(paths: data.Paths) -> None:
    from csm import app as appmod
    summary.refresh = lambda *a, **k: summary.Digests(paths.summaries)  # no model calls
    prs.fetch = lambda url: None  # no gh: the made-up PR states in the cache stay
    OUT.mkdir(parents=True, exist_ok=True)
    app = appmod.CSM(paths, theme="textual-light", notifications=False)
    async with app.run_test(size=(150, 42)) as pilot:
        await pilot.app.workers.wait_for_complete()
        app.waiting[next(s.id for s in app.sessions if s.title.startswith("Add Apple Pay"))] = NOW - 9 * 60
        app.rebuild()
        from csm.widgets import SessionList
        app.query_one(SessionList).highlighted = 0  # the Summary row, so the preview shows the page
        await pilot.pause(0.8)
        app.save_screenshot("summary.svg", str(OUT))
        app.focus_id = next(s.id for s in app.sessions if s.title.startswith("Fix the checkout"))
        app.rebuild()
        await pilot.pause(1.0)
        app.save_screenshot("sessions.svg", str(OUT))
        await pilot.press("I")
        await pilot.pause(0.5)
        app.save_screenshot("stats.svg", str(OUT))


def main() -> None:
    import shutil
    os.environ["CSM_STUCK_MINUTES"] = "0"
    os.environ["XDG_STATE_HOME"] = str(HOME / ".local" / "state")
    try:
        paths = build(HOME)
        asyncio.run(shoot(paths))
    finally:
        shutil.rmtree(HOME, ignore_errors=True)
    for p in sorted(OUT.glob("*.svg")):
        print(p.relative_to(ROOT))


if __name__ == "__main__":
    main()
