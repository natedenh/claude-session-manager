"""Cost view: where the money went, from each session's final cost-state."""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static

from .data import Session

DAY = 86400
WEEKS = 12
TOP = 10


@dataclass
class Totals:
    all_time: float
    week: float
    month: float
    count: int


def priced(sessions: Iterable[Session]) -> list[Session]:
    return [s for s in sessions if s.cost is not None]


def totals(sessions: Iterable[Session], now: float) -> Totals:
    ss = priced(sessions)
    within = lambda days: sum(s.cost for s in ss if s.mtime >= now - days * DAY)
    return Totals(sum(s.cost for s in ss), within(7), within(30), len(ss))


def by_project(sessions: Iterable[Session]) -> list[tuple[str, int, float]]:
    acc: dict[str, list] = {}
    for s in priced(sessions):
        row = acc.setdefault(s.project, [s.project_name, 0, 0.0])
        row[1] += 1
        row[2] += s.cost
    return sorted((tuple(r) for r in acc.values()), key=lambda r: -r[2])


def week_start(ts: float) -> date:
    d = datetime.fromtimestamp(ts).date()
    return d - timedelta(days=d.weekday())


def by_week(sessions: Iterable[Session], now: float, weeks: int = WEEKS) -> list[tuple[date, float]]:
    """Newest week first; weeks with no spend are kept so gaps are visible."""
    this = week_start(now)
    starts = [this - timedelta(weeks=i) for i in range(weeks)]
    acc = dict.fromkeys(starts, 0.0)
    for s in priced(sessions):
        if (w := week_start(s.mtime)) in acc:
            acc[w] += s.cost
    return [(w, acc[w]) for w in starts]


def top_sessions(sessions: Iterable[Session], n: int = TOP) -> list[Session]:
    return sorted(priced(sessions), key=lambda s: -s.cost)[:n]


def money(x: float) -> str:
    return f"${x:,.2f}"


def heading(text: str) -> Text:
    return Text("\n" + text, style="bold")


def table(*cols: tuple[str, str]) -> Table:
    t = Table(box=None, show_header=True, header_style="dim", padding=(0, 2, 0, 0), expand=False)
    for name, justify in cols:
        t.add_column(name, justify=justify, no_wrap=True, overflow="ellipsis")
    return t


def render(sessions: list[Session], archived: Callable[[Session], object], ago: Callable[[float], str],
           now: float | None = None) -> Group:
    now = time.time() if now is None else now
    tot = totals(sessions, now)
    head = table(("All time", "right"), ("Last 7 days", "right"), ("Last 30 days", "right"), ("Sessions", "right"))
    head.add_row(money(tot.all_time), money(tot.week), money(tot.month), str(tot.count))

    proj = table(("Project", "left"), ("Sessions", "right"), ("Total", "right"))
    for name, n, total in by_project(sessions):
        proj.add_row(name, str(n), money(total))

    wk = table(("Week of", "left"), ("Total", "right"))
    for start, total in by_week(sessions, now):
        wk.add_row(start.isoformat(), Text(money(total), style="" if total else "dim"))

    top = table(("Cost", "right"), ("Session", "left"), ("Project", "left"), ("Last active", "right"))
    for s in top_sessions(sessions):
        style = "dim" if archived(s) else ""
        top.add_row(money(s.cost), s.title, s.project_name, ago(s.mtime), style=style)

    return Group(
        head,
        heading("By project"), proj,
        heading("By week"),
        Text("Each session's whole cost counts in the week of its last activity.", style="dim"),
        wk,
        heading("Top sessions"), top,
    )


class Costs(Screen[None]):
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close", show=False),
        Binding("dollar_sign", "close", "Close", show=False),
    ]
    DEFAULT_CSS = """
    Costs #body { padding: 1 2; }
    """

    def __init__(self, sessions: list[Session], archived: Callable[[Session], object],
                 ago: Callable[[float], str]):
        super().__init__()
        self.sessions, self.archived, self.ago = sessions, archived, ago

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="body"):
            yield Static(render(self.sessions, self.archived, self.ago))
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#body").focus()

    def action_close(self) -> None:
        self.dismiss(None)
