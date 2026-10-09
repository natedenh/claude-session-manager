"""The Routines screens: every routine, then one routine with its instructions and runs."""
from __future__ import annotations

from datetime import datetime

from rich.console import Group
from rich.markdown import Markdown
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from .routines import Routine, until, when

STATUS_STYLE = {"active": "green", "scheduled": "cyan", "paused": "dim", "completed": "dim", "off": "dim"}
RESULT_STYLE = {"needs_input": "bold", "review_ready": "", "error": "red"}


def gist(text: str, limit: int = 120) -> str:
    """The first sentence of a result, for a one-line row; the preview has all of it."""
    text = " ".join(text.split())
    end = next((i + 1 for i, ch in enumerate(text) if ch in ".!?" and (i + 1 == len(text) or text[i + 1] == " ")), len(text))
    first = text[:end]
    return first if len(first) <= limit else first[:limit - 1].rstrip() + "…"


def status_line(r: Routine, now: datetime | None = None) -> Text:
    nxt = r.next_run(now)
    parts = [(r.status, STATUS_STYLE.get(r.status, "")), (f" · {r.schedule}", "dim")]
    if nxt:
        parts.append((f" · next {until(nxt, now)}", "cyan"))
    if r.last_run:
        parts.append((f" · last {when(datetime.fromtimestamp(r.last_run), now)}", "dim"))
    return Text.assemble(*parts)


def overview(rs: list[Routine], now: datetime | None = None) -> Group:
    """Every routine, briefly: the preview pane's view of the Routines row."""
    out: list = [Text("Routines", style="bold"),
                 Text("Run by the Claude desktop app, which keeps running them while your computer is awake. "
                      "csm shows them; change them in the app.", style="dim"), Text("")]
    if not rs:
        out.append(Text("No routines yet. Make one in the Claude desktop app (Code ▸ Routines).", style="dim italic"))
    for r in rs:
        out += [Text.assemble(("◷ ", "cyan"), (r.name, "bold"), (" (Cowork)" if r.kind == "cowork" else "", "dim")),
                Text.assemble("  ", status_line(r, now))]
        if r.runs and (last := r.runs[0]).result:
            out.append(Text(f"  last result: {gist(last.result)}", style="dim"))
        out.append(Text(""))
    return Group(*out)


def detail(r: Routine) -> Group:
    rows: list = [Text(r.name, style="bold"), status_line(r)]
    if r.description:
        rows += [Text(""), Text(r.description)]
    rows += [Text(""), Text.assemble(("folder ", "dim"), r.cwd or "—"),
             Text.assemble(("instructions ", "dim"), (r.path, "dim"))]
    if r.instructions:
        rows += [Text(""), Markdown(r.instructions)]
    rows += [Text(""), Text(f"Runs ({len(r.runs)})" if r.runs else "No runs yet", style="bold")]
    return Group(*rows)


def run_row(rec, now: datetime | None = None) -> Text:
    t = when(datetime.fromtimestamp(rec.created), now) if rec.created else "?"
    return Text.assemble((f"{t:<22}", "dim"), (gist(rec.result) if rec.result else "(no summary)", RESULT_STYLE.get(rec.result_kind or "", "")))


class RoutineView(Screen[str | None]):
    """One routine. Enter on a run closes the screens and shows that session in the list."""
    BINDINGS = [Binding("escape", "close", "Back"), Binding("q", "close", show=False)]
    DEFAULT_CSS = """
    RoutineView #info { height: auto; max-height: 60%; padding: 1 2 0 2; }
    RoutineView #runs { height: 1fr; border: none; padding: 0 2; }
    """

    def __init__(self, routine: Routine, known: set[str]):
        super().__init__()
        self.routine, self.known = routine, known  # known: session ids csm has a transcript for

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="info"):
            yield Static(detail(self.routine))
        yield OptionList(*[Option(run_row(rec) if rec.cli_id in self.known else
                                  Text.assemble(run_row(rec), ("  (transcript not here)", "dim")),
                                  id=rec.cli_id or f"x{i}", disabled=rec.cli_id not in self.known)
                           for i, rec in enumerate(self.routine.runs)], id="runs")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#runs").focus()

    @on(OptionList.OptionSelected, "#runs")
    def chosen(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_close(self) -> None:
        self.dismiss(None)


class Routines(Screen[str | None]):
    """Every routine; enter opens one. Dismisses with a session id to show, or None."""
    BINDINGS = [Binding("escape", "close", "Close"), Binding("q", "close", show=False)]
    DEFAULT_CSS = """
    Routines #list { height: 1fr; border: none; padding: 1 2; }
    """

    def __init__(self, routines: list[Routine], known: set[str]):
        super().__init__()
        self.routines, self.known = routines, known

    def compose(self) -> ComposeResult:
        options: list = []
        for r in self.routines:
            options.append(Option(Group(Text.assemble(("◷ ", "cyan"), (r.name, "bold"),
                                                      (" (Cowork)" if r.kind == "cowork" else "", "dim")),
                                        Text.assemble("  ", status_line(r))), id=r.id))
        if not options:
            options.append(Option(Text("No routines yet. Make one in the Claude desktop app (Code ▸ Routines).",
                                       style="dim italic"), disabled=True))
        yield OptionList(*options, id="list")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#list").focus()

    @on(OptionList.OptionSelected, "#list")
    def chosen(self, event: OptionList.OptionSelected) -> None:
        r = next(x for x in self.routines if x.id == event.option.id)

        def back(sid: str | None) -> None:
            if sid:
                self.dismiss(sid)
        self.app.push_screen(RoutineView(r, self.known), back)

    def action_close(self) -> None:
        self.dismiss(None)
