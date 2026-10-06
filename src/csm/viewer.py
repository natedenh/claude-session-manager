"""Full transcript viewer: the whole conversation of one session, scrollable and searchable."""
from __future__ import annotations

from rich.cells import cell_len
from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
from rich.style import Style
from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.geometry import Size
from textual.message import Message
from textual.screen import Screen
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widgets import Footer, Input, Static
from textual.worker import get_current_worker

from .data import Session
from .export import conversation

MAX_CHARS = 20000  # per message; the rest of a huge paste isn't worth laying out
MATCH = Style(reverse=True)
CURRENT = Style(bold=True, color="black", bgcolor="yellow")


def blocks(turns: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Fold each run of tool calls into one ("tools", summary) block."""
    out: list[tuple[str, str]] = []
    names: list[str] = []
    for role, text in turns + [("end", "")]:
        if role == "tools":
            names.append(text)
            continue
        if names:
            n = len(names)
            out.append(("tools", f"ran {n} tool call{'s' * (n != 1)}: {', '.join(dict.fromkeys(names))}"))
            names = []
        if role != "end":
            out.append((role, text))
    return out


def renderable(role: str, text: str, dark: bool) -> RenderableType:
    if role == "tools":
        return Text("  " + text, style="dim")
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + f"\n\n… {len(text) - MAX_CHARS:,} more characters"
    user = role == "user"
    body = Markdown(text, style="bold" if user else "none", code_theme="ansi_dark" if dark else "ansi_light")
    row = Table.grid(expand=True)
    row.add_column(width=2)
    row.add_column(ratio=1)
    row.add_row(Text("❯", style="bold cyan") if user else Text("●", style="dim"), body)
    return row


def layout(items: list[tuple[str, str]], width: int, dark: bool) -> list[Strip]:
    console = Console(width=max(width, 10), color_system=None, legacy_windows=False)
    group = Group(*[g for role, text in items for g in (renderable(role, text, dark), Text(""))])
    return [Strip(line) for line in console.render_lines(group, pad=False)]


def find(lines: list[Strip], query: str) -> list[tuple[int, int, int]]:
    """Every case-insensitive occurrence as (line, start cell, end cell)."""
    q = query.lower()
    hits = []
    for y, strip in enumerate(lines):
        text = strip.text.lower()
        i = text.find(q)
        while i >= 0:
            hits.append((y, cell_len(text[:i]), cell_len(text[:i + len(q)])))
            i = text.find(q, i + len(q))
    return hits


class Transcript(ScrollView, can_focus=True):
    def __init__(self, dark: bool = True):
        super().__init__()
        self.dark = dark
        self.items: list[tuple[str, str]] | None = None
        self.lines: list[Strip] = []
        self.hits: list[tuple[int, int, int]] = []
        self.by_line: dict[int, list[tuple[int, int, int]]] = {}
        self.query = ""
        self.pos = -1  # index into hits of the current match
        self.width_laid = 0
        self.first = True

    def set_items(self, items: list[tuple[str, str]]) -> None:
        self.items = items
        self.relayout()

    def on_resize(self) -> None:
        if self.items is not None and self.text_width() != self.width_laid:
            self.relayout()

    def text_width(self) -> int:
        # Leave room for the vertical scrollbar, or the last characters of long lines are cut.
        return self.size.width - self.styles.scrollbar_size_vertical - 1

    @work(thread=True, exclusive=True, group="layout")
    def relayout(self) -> None:
        width = self.text_width()
        if width <= 0 or self.items is None:
            return
        lines = layout(self.items, width, self.dark)
        if not get_current_worker().is_cancelled:
            self.app.call_from_thread(self.apply, lines, width)

    def apply(self, lines: list[Strip], width: int) -> None:
        frac = self.scroll_y / max(self.max_scroll_y, 1)
        self.lines, self.width_laid = lines, width
        self.virtual_size = Size(width, len(lines))
        if self.query:
            self.search(self.query, keep=True)
        self.refresh()
        if self.first:
            self.call_after_refresh(self.scroll_end, animate=False)
        else:
            self.call_after_refresh(lambda: self.scroll_to(y=round(frac * self.max_scroll_y), animate=False))
        self.first = False

    def render_line(self, y: int) -> Strip:
        row = y + self.scroll_offset.y
        width = self.size.width
        if row >= len(self.lines):
            return Strip.blank(width)
        strip = self.lines[row]
        spans = self.by_line.get(row)
        if spans:
            out, at = [], 0
            for i, s, e in spans:
                out += [strip.crop(at, s), strip.crop(s, e).apply_style(CURRENT if i == self.pos else MATCH)]
                at = e
            strip = Strip.join(out + [strip.crop(at)])
        return strip.adjust_cell_length(width)

    # ---- search ----------------------------------------------------------

    def search(self, query: str, keep: bool = False) -> None:
        self.query = query
        self.hits = find(self.lines, query) if query else []
        self.by_line = {}
        for i, (y, s, e) in enumerate(self.hits):
            self.by_line.setdefault(y, []).append((i, s, e))
        if not keep:
            self.pos = 0 if self.hits else -1
        else:
            self.pos = min(max(self.pos, 0), len(self.hits) - 1) if self.hits else -1
        self.show_current()

    def step(self, by: int) -> None:
        if self.hits:
            self.pos = (self.pos + by) % len(self.hits)
            self.show_current()

    def show_current(self) -> None:
        if self.pos >= 0:
            y = self.hits[self.pos][0]
            if not self.scroll_offset.y <= y < self.scroll_offset.y + self.size.height:
                self.scroll_to(y=max(y - self.size.height // 3, 0), animate=False)
        self.refresh()
        self.post_message(self.Moved())

    def clear(self) -> None:
        self.search("")

    class Moved(Message):
        """The matches or the current match changed."""


class Viewer(Screen[None]):
    BINDINGS = [
        Binding("escape", "escape", "Close"),
        Binding("q", "close", "Close", show=False),
        Binding("slash", "find", "Search"),
        Binding("n", "step(1)", "Next", show=False),
        Binding("N", "step(-1)", "Prev", show=False),
        Binding("g", "top", "Top", show=False),
        Binding("G", "bottom", "Bottom", show=False),
        Binding("j", "scroll(1)", show=False),
        Binding("k", "scroll(-1)", show=False),
    ]
    DEFAULT_CSS = """
    Viewer #head { height: auto; padding: 0 2; text-style: bold; }
    Viewer Transcript { padding: 0 1; height: 1fr; overflow-x: hidden; }
    Viewer #loading { padding: 1 2; color: $text-muted; }
    Viewer #find { display: none; margin: 0 1; border-title-color: $accent; }
    Viewer #find.-on { display: block; }
    Viewer #count { height: 1; padding: 0 2; color: $text-muted; }
    """

    def __init__(self, session: Session, dark: bool = True):
        super().__init__()
        self.session, self.dark = session, dark

    def compose(self) -> ComposeResult:
        s = self.session
        meta = " · ".join(p for p in (s.title, s.project_name, s.branch) if p)
        yield Static(Text(meta, overflow="ellipsis", no_wrap=True), id="head")
        yield Static("Loading transcript…", id="loading")
        yield Transcript(self.dark)
        yield Input(id="find", placeholder="search this transcript (enter)")
        yield Static("", id="count")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(Transcript).display = False
        self.query_one("#find", Input).border_title = "search"
        self.load()

    @work(thread=True)
    def load(self) -> None:
        items = blocks(conversation(self.session.path))
        self.app.call_from_thread(self.loaded, items)

    def loaded(self, items: list[tuple[str, str]]) -> None:
        view = self.query_one(Transcript)
        if not items:
            self.query_one("#loading", Static).update("(no messages)")
            return
        view.display = True
        self.query_one("#loading").display = False
        view.focus()
        view.set_items(items)

    @property
    def view(self) -> Transcript:
        return self.query_one(Transcript)

    def update_count(self) -> None:
        v = self.view
        if not v.query:
            text = ""
        elif v.hits:
            text = f"“{v.query}”  {v.pos + 1}/{len(v.hits)}    n / N next / previous"
        else:
            text = f"“{v.query}”  no matches"
        self.query_one("#count", Static).update(text)

    @on(Transcript.Moved)
    def moved(self) -> None:
        self.update_count()

    def action_find(self) -> None:
        box = self.query_one("#find", Input)
        box.add_class("-on")
        box.value = self.view.query
        box.focus()

    @on(Input.Submitted, "#find")
    def submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        self.query_one("#find").remove_class("-on")
        self.view.focus()
        self.view.search(query)

    def action_step(self, by: int) -> None:
        self.view.step(by)

    def action_top(self) -> None:
        self.view.scroll_home(animate=False)

    def action_bottom(self) -> None:
        self.view.scroll_end(animate=False)

    def action_scroll(self, by: int) -> None:
        self.view.scroll_relative(y=by, animate=False)

    def action_escape(self) -> None:
        box = self.query_one("#find", Input)
        if box.has_class("-on") or self.view.query:
            box.remove_class("-on")
            self.view.clear()
            self.view.focus()
        else:
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
