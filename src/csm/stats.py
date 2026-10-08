"""Stats: how you've been using Claude, from the per-day history each session records."""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static

from .costs import heading, money, table
from .data import Session
from .usage import FEATURES, LEARNING_DAYS, Usage

DAYS = 14
WEEKS = 8
TOP = 8
EIGHTHS = " ▏▎▍▌▋▊▉"
SPARKS = "▁▂▃▄▅▆▇█"
BAR = 28  # widest bar, in cells


@dataclass
class Day:
    minutes: int = 0
    cost: float = 0.0
    lines: int = 0
    sessions: set[str] = field(default_factory=set)


def rate(sessions: Iterable[Session]) -> float:
    """Dollars per unit of token weight, learned from the cost Claude Code recorded: your own
    prices, whatever provider bills them. Prices activity since a session's last cost record."""
    sessions = list(sessions)
    weight = sum(s.priced_weight for s in sessions)
    return sum(sum(s.day_cost.values()) for s in sessions if s.priced_weight) / weight if weight else 0.0


def spend(s: Session, day: str, per_weight: float) -> float:
    return s.day_cost.get(day, 0.0) + s.day_unpriced.get(day, 0.0) * per_weight


def daily(sessions: Iterable[Session], per_weight: float | None = None) -> dict[date, Day]:
    sessions = list(sessions)
    per_weight = rate(sessions) if per_weight is None else per_weight
    out: dict[date, Day] = {}
    for s in sessions:
        for d, m in s.active.items():
            day = out.setdefault(date.fromisoformat(d), Day())
            day.minutes += m
            day.sessions.add(s.id)
        for d in s.day_cost.keys() | s.day_unpriced.keys():
            out.setdefault(date.fromisoformat(d), Day()).cost += spend(s, d, per_weight)
        for d, n in s.day_lines.items():
            out.setdefault(date.fromisoformat(d), Day()).lines += n
    return out


def streaks(days: Iterable[date], today: date) -> tuple[int, int]:
    """(current, longest) runs of consecutive active days. Today not being active yet doesn't break it."""
    active = set(days)
    longest = run = 0
    prev = None
    for d in sorted(active):
        run = run + 1 if prev and d - prev == timedelta(days=1) else 1
        longest, prev = max(longest, run), d
    start = today if today in active else today - timedelta(days=1)
    current = 0
    while start - timedelta(days=current) in active:
        current += 1
    return current, longest


def span(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def bar(value: float, top: float, width: int = BAR) -> str:
    """A horizontal bar in eighths of a cell."""
    if top <= 0 or value <= 0:
        return ""
    cells = max(1 / 8, value / top * width)
    whole, part = int(cells), round((cells - int(cells)) * 8)
    return "█" * whole + (EIGHTHS[part] if 0 < part < 8 else "█" if part == 8 else "")


def totals(days: dict[date, Day], start: date, end: date) -> Day:
    out = Day()
    for d, day in days.items():
        if start <= d <= end:
            out.minutes += day.minutes
            out.cost += day.cost
            out.lines += day.lines
            out.sessions |= day.sessions
    return out


def yours(usage: Usage, today: date) -> list:
    """How you use csm: favourites, features never tried, ones you've drifted from."""
    out: list = [heading("Your csm")]
    top = sorted(((usage.since_days(f.action, 7), f) for f in FEATURES), key=lambda x: -x[0])
    if used := [(n, f) for n, f in top if n][:8]:
        t = table(("Most used, last 7 days", "left"), ("Keys", "left"), ("Times", "right"))
        for n, f in used:
            t.add_row(f.what, f.keys, str(n))
        out.append(t)
    if usage.history_days < LEARNING_DAYS:
        days = usage.history_days
        out.append(Text(f"Still learning how you use csm ({days} day{'s' * (days != 1)} so far); "
                        "suggestions show up after a few days.", style="dim"))
        return out
    never = [f for f in FEATURES if not usage.total(f.action) and f.action != "open"]
    if never:
        t = table(("Not tried yet", "left"), ("Keys", "left"))
        for f in never[:12]:
            t.add_row(f.what, Text(f.keys, style="bold"))
        out.append(t)
        if len(never) > 12:
            out.append(Text(f"and {len(never) - 12} more; ? lists every key.", style="dim"))
    stale = (today - timedelta(days=14)).isoformat()
    if drifted := [f for f in FEATURES if (last := usage.last(f.action)) and last < stale]:
        t = table(("Not lately", "left"), ("Keys", "left"), ("Last used", "right"))
        for f in drifted:
            t.add_row(f.what, f.keys, usage.last(f.action))
        out.append(t)
    return out


def render(sessions: list[Session], today: date | None = None, usage: Usage | None = None) -> Group:
    today = today or date.today()
    per_weight = rate(sessions)
    days = daily(sessions, per_weight)
    if not days:
        return Group(Text("No history yet: sessions record it as they're read.", style="dim italic"))
    monday = today - timedelta(days=today.weekday())
    cur, longest = streaks((d for d, x in days.items() if x.minutes), today)

    head = table(("", "left"), ("Today", "right"), ("This week", "right"), ("Last week", "right"))
    periods = [totals(days, today, today), totals(days, monday, today),
               totals(days, monday - timedelta(days=7), monday - timedelta(days=1))]
    head.add_row("Claude time", *[span(p.minutes) for p in periods])
    head.add_row("Sessions", *[str(len(p.sessions)) for p in periods])
    head.add_row("Cost", *[money(p.cost) for p in periods])
    head.add_row("Lines added", *[f"{p.lines:,}" for p in periods])
    streak = Text.assemble(("Streak ", "bold"), (f"{cur} day{'s' * (cur != 1)}", "bold green" if cur else "dim"),
                           (f"   longest {longest}", "dim"))

    recent = [today - timedelta(days=i) for i in range(DAYS)]
    top = max((days.get(d, Day()).minutes for d in recent), default=0)
    per_day = Table.grid(padding=(0, 2))
    for col in ("left", "left", "right", "right", "right"):
        per_day.add_column(justify=col)
    for d in recent:
        x = days.get(d, Day())
        style = "bold" if d == today else "dim" if not x.minutes else ""
        per_day.add_row(Text(d.strftime("%a %b %-d"), style=style), Text(bar(x.minutes, top), style="green"),
                        span(x.minutes) if x.minutes else "", f"{len(x.sessions)} sess" if x.sessions else "",
                        money(x.cost) if x.cost else "")

    weekly = Table.grid(padding=(0, 2))
    for col in ("left", "left", "right"):
        weekly.add_column(justify=col)
    weeks = [(monday - timedelta(weeks=i), totals(days, monday - timedelta(weeks=i),
                                                   monday - timedelta(weeks=i) + timedelta(days=6)))
             for i in range(WEEKS)]
    first = min(days)
    weeks = [(start, w) for start, w in weeks if start + timedelta(days=6) >= first]  # before that: no record
    top_cost = max((w.cost for _, w in weeks), default=0)
    for start, w in weeks:
        weekly.add_row(start.strftime("%b %-d"), Text(bar(w.cost, top_cost), style="yellow"),
                       Text(money(w.cost), style="" if w.cost else "dim"))

    projects: dict[str, list] = {}
    for s in sessions:
        m = sum(v for d, v in s.active.items() if date.fromisoformat(d) >= monday)
        c = sum(spend(s, d, per_weight) for d in s.day_cost.keys() | s.day_unpriced.keys()
                if date.fromisoformat(d) >= monday)
        if m or c:
            row = projects.setdefault(s.project, [s.project_name, 0, 0.0])
            row[1] += m
            row[2] += c
    proj = table(("Project", "left"), ("Claude time", "right"), ("Cost", "right"))
    for name, m, c in sorted(projects.values(), key=lambda r: -r[1])[:TOP]:
        proj.add_row(name, span(m), money(c))

    hours = [sum(s.hours[h] for s in sessions) for h in range(24)]
    peak = max(hours) or 1
    clock = Text.assemble(
        ("".join(SPARKS[round(h / peak * (len(SPARKS) - 1))] if h else " " for h in hours), "cyan"), "\n",
        ("0     6     12    18   23", "dim"))

    skills = Counter()
    for s in sessions:
        skills.update(s.skills)
    sk = table(("Skill", "left"), ("Used", "right"))
    for name, n in skills.most_common(TOP):
        sk.add_row(name, f"{n}×")

    out = [head, Text(""), streak,
           heading(f"Last {DAYS} days"), per_day,
           heading("Busiest projects this week"), proj if projects else Text("Nothing yet this week.", style="dim"),
           heading("Cost by week"), weekly,
           heading("When you work"), Text("Active minutes by hour of the day, all time.", style="dim"), clock,
           heading("Most-used skills"), sk if skills else Text("No skills used yet.", style="dim")]
    if usage is not None:
        out += yours(usage, today)
    out.append(Text("\nClaude time adds up each session's active minutes, so sessions running side by side "
                    "count separately. Cost is what Claude Code recorded, spread over the days by tokens; "
                    "for work since a session's last record, it's estimated at your average price.", style="dim"))
    return Group(*out)


class Stats(Screen[None]):
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close", show=False),
        Binding("I", "close", "Close", show=False),
    ]
    DEFAULT_CSS = """
    Stats #body { padding: 1 2; }
    """

    def __init__(self, sessions: list[Session], usage: Usage | None = None):
        super().__init__()
        self.sessions, self.usage = sessions, usage

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="body"):
            yield Static(render(self.sessions, usage=self.usage))
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#body").focus()

    def action_close(self) -> None:
        self.dismiss(None)
