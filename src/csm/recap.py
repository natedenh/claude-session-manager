"""A day's recap as a Markdown note: time, cost and what each session got done.

What got done and decided comes from the Summary page's digests (refreshed first), so a
session without one falls back to the end of its last message.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from .data import Session
from .stats import daily, rate, span, spend, streaks
from .summary import Digests

SAID_CHARS = 240


def money(x: float) -> str:
    return f"${x:,.2f}"


def render(sessions: list[Session], day: date, digests: Digests, last_said: Callable[[Session], str | None],
           pr_text: Callable[[Session], str] = lambda s: "", waiting: set[str] = frozenset()) -> str:
    key = day.isoformat()
    today = [s for s in sessions if s.active.get(key)]
    per_weight = rate(sessions)
    days = daily(sessions, per_weight)
    totals = days.get(day)
    minutes = totals.minutes if totals else 0
    cost = totals.cost if totals else 0.0
    lines = totals.lines if totals else 0
    streak, _ = streaks((d for d, x in days.items() if x.minutes), day)
    projects: dict[str, list[Session]] = {}
    for s in today:
        projects.setdefault(s.project, []).append(s)
    mins = lambda ss: sum(s.active.get(key, 0) for s in ss)
    spent = lambda ss: sum(spend(s, key, per_weight) for s in ss)

    out = ["---", f"date: {key}", f"claude_minutes: {minutes}", f"sessions: {len(today)}",
           f"cost: {cost:.2f}", f"lines_added: {lines}", "---", "",
           f"# Claude recap: {day.strftime('%A, %B %-d, %Y')}", ""]
    if not today:
        out.append("No Claude activity recorded this day.")
        return "\n".join(out) + "\n"
    out.append(f"{span(minutes)} of Claude time across {len(today)} session{'s' * (len(today) != 1)} in "
               f"{len(projects)} project{'s' * (len(projects) != 1)} · {money(cost)} · {lines:,} lines added"
               + (f" · {streak}-day streak" if streak > 1 else ""))

    if needs := [s for s in today if s.id in waiting or ((d := digests.get(s)) and d.status == "needs_input")]:
        out += ["", "## Needs you", ""]
        for s in needs:
            d = digests.get(s)
            out.append(f"- **{s.title}** ({s.project_name}): {(d.question if d and d.question else None) or 'waiting for you'}")

    for project, ss in sorted(projects.items(), key=lambda kv: -mins(kv[1])):
        out += ["", f"## {ss[0].project_name} · {span(mins(ss))} · {money(spent(ss))}"]
        for s in sorted(ss, key=lambda s: -s.active.get(key, 0)):
            facts = [span(s.active[key]), money(spend(s, key, per_weight))]
            if s.pr_number:
                facts.append(f"[PR #{s.pr_number}]({s.pr_url})" + (f" {t}" if (t := pr_text(s)) else ""))
            if s.branch and s.branch not in ("main", "master", "HEAD"):
                facts.append(f"`{s.branch}`")
            out += ["", f"### {s.title}", " · ".join(facts), ""]
            d = digests.get(s)
            if d and d.headline:
                out.append(f"*{d.headline}*")
                out.append("")
            items = ([f"- Done: {x}" for x in d.done] + [f"- Decided: {x}" for x in d.decisions]) if d else []
            if items:
                out += items
            elif said := last_said(s):
                said = " ".join(said.split())
                out.append(f"- Last: {said[:SAID_CHARS]}{'…' if len(said) > SAID_CHARS else ''}")
    return "\n".join(out) + "\n"


def write(text: str, day: date, directory: Path) -> Path:
    """`<dir>/<date> Claude recap.md`; writing the same day again replaces it."""
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / f"{day.isoformat()} Claude recap.md"
    dest.write_text(text, encoding="utf-8")
    return dest
