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
HOME = Path(tempfile.mkdtemp(prefix="csm-demo-"))
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
TURNS = {
    0: [("user", "Two tabs can both pay for the same cart. Find out why and fix it."),
        ("assistant", "Both tabs read the cart, then each **creates its own charge**. Nothing locks the cart between "
                      "the read and the charge.\n\nI'll make the charge idempotent on `cart_id`:\n\n"
                      "1. Add a unique index on `charges(cart_id)`\n2. Catch the conflict and return the first charge\n"
                      "3. Add a test that pays from two tasks at once"),
        ("user", "Go ahead, and open a PR when the test passes."),
        ("assistant", "The new test reproduces it: two charges for one cart. Adding the index and the conflict "
                      "handling now, then running the suite.")],
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
        # A week of activity, so the sparklines and stats have something to show.
        days = 1 + (i * 3) % 7
        for d in range(days, -1, -1):
            for m in range(0, 20 + (i * 7 + d * 11) % 70, 3):
                ts = last - d * 86400 - m * 60
                lines.append({"type": "assistant", "timestamp": iso(ts), "cwd": cwd,
                              "message": {"id": f"msg_{i}_{d}_{m}", "role": "assistant", "content": [],
                                          "usage": {"input_tokens": 400, "cache_creation_input_tokens": 3000,
                                                    "cache_read_input_tokens": 40000, "output_tokens": 900}}})
        turns = TURNS.get(i, [("user", title), ("assistant", "On it. I'll start by reading the code involved.")])
        for role, text in turns:
            lines.append({"type": role, "timestamp": iso(last), "cwd": cwd,
                          "message": {"role": role, "content": text if role == "user" else [{"type": "text", "text": text}]}})
        lines.insert(0, {"type": "custom-title", "customTitle": title})
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
    app = appmod.CSM(paths, theme="textual-dark", notifications=False)
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
