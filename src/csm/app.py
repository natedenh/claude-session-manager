"""csm — browse, search and resume Claude Code sessions."""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import time
from datetime import datetime

from rich.console import Group
from rich.markdown import Markdown
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.theme import Theme as RichTheme
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, Label, OptionList, Static
from textual.widgets.option_list import Option
from textual.worker import get_current_worker

from . import data, desktop, export, ghostty, launch, notify, prs, tmux
from .costs import Costs
from .data import LiveSession, Message, Session
from .launch import Launch, Pending
from .worktrees import Worktrees

PER_PROJECT = 5
PINNED = "__pinned__"  # pseudo-project key for the Pinned group
STATUS_STYLE = {"idle": "green", "busy": "yellow"}
FILTER_NAMES = {"pr": "PRs", "worktree": "worktrees", "live": "live", "waiting": "waiting"}
MAX_MESSAGE_CHARS = 2500
THEMES = ("ansi-light", "ansi-dark")  # Textual's themes that use the terminal's own colors


def default_theme() -> str:
    """Match the terminal: its background shows through, so pick light or dark from macOS."""
    if theme := os.environ.get("CSM_THEME"):
        return theme
    r = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True)
    return "ansi-dark" if r.stdout.strip() == "Dark" else "ansi-light"

HELP = """\
[b]Navigate[/b]
  ↑↓ j k    move                          [ ]       previous / next project
  enter     open a session; on a project header, collapse / expand it
  e         show every session (5 per project otherwise)
  v         flat newest-first list / grouped by project

[b]Find[/b]
  /         filter by title, project or branch as you type
  s         search transcript text (enter runs it)
  esc       clear the filter, search and marks
  p w l !   only PR-linked / worktree / live / waiting-for-you sessions
  a         also show archived sessions (archived in csm or Claude desktop)

[b]Open[/b]
  enter     beside this list in tmux, otherwise here (back to the list when claude exits).
            A session already open in a Ghostty tab or Claude desktop is shown there.
  ctrl+\\    (tmux) switch focus between this list and the session
  n / N     new session in the highlighted project / in a new worktree
  f         fork the highlighted session
  o / O     resume in a new Ghostty tab / window
  c         (tmux) stop the session's claude process

[b]Manage[/b]
  r         rename                        y         copy session id
  x         archive / unarchive           d         move transcript to the Trash
  E         export to Markdown (marked, or highlighted)
  *         pin / unpin                   space     mark; x and d act on all marked
  $         costs                         W         clean up worktrees
  ctrl+r    reload
  q         quit (in tmux: detach; sessions keep running)

[b]Icons[/b]
  [green]⇄[/] PR linked: [yellow]pending[/], [red]failing[/], [magenta]merged[/], [dim]closed / draft[/]    [magenta]⑂[/] worktree   [dim]○[/] other
  [green]●[/] live, idle   [yellow]●[/] live, busy   ▶ shown beside the list   ◆ waiting for you
"""


def pr_style(st: prs.PRStatus | None) -> str:
    """Icon color for a PR; plain green when its status is unknown."""
    if st is None:
        return "green"
    if st.state == "merged":
        return "magenta"
    if st.state in ("closed", "draft"):
        return "dim"
    if st.checks == "failing" or st.review == "changes_requested":
        return "red"
    if st.checks == "pending" or st.review == "review_required":
        return "yellow"
    return "green"


def pr_summary(st: prs.PRStatus) -> str:
    parts = [st.state]
    if st.checks != "none" and not st.final:
        parts.append(f"checks {st.passed}/{st.total} {st.checks}")
    if st.review and not st.final:
        parts.append(st.review.replace("_", " "))
    return " · ".join(parts)


def ago(ts: float) -> str:
    d = time.time() - ts
    for unit, sec in (("d", 86400), ("h", 3600), ("m", 60)):
        if d >= sec:
            return f"{int(d // sec)}{unit} ago"
    return "just now"


def tilde(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home) else path


class SessionList(OptionList):
    BINDINGS = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
    ]


class Prompt(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(self, title: str, value: str = ""):
        super().__init__()
        self.title_text, self.value = title, value

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text)
            yield Input(value=self.value, select_on_focus=True)

    @on(Input.Submitted)
    def submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class Confirm(ModalScreen[bool]):
    BINDINGS = [
        Binding("y", "answer(True)", show=False),
        Binding("enter", "answer(True)", show=False),
        Binding("n", "answer(False)", show=False),
        Binding("escape", "answer(False)", show=False),
    ]

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.message)
            yield Label("[dim]y / enter = yes    n / esc = no[/]")

    def action_answer(self, yes: bool) -> None:
        self.dismiss(yes)


class Help(ModalScreen[None]):
    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(HELP)

    def on_key(self) -> None:
        self.dismiss(None)


class CSM(App[Session | None]):
    TITLE = "Claude sessions"
    # Beside a session in tmux the list is all that fits; full width gets the preview back.
    HORIZONTAL_BREAKPOINTS = [(0, "-narrow"), (100, "-wide")]
    CSS = """
    #search { margin: 0 1; border-title-color: $accent; }
    #body { height: 1fr; }
    #list { width: 38%; min-width: 34; max-width: 72; border: none; padding: 0; }
    #list { text-wrap: nowrap; text-overflow: ellipsis; }
    #right { border-left: solid $foreground 30%; padding: 0 1 0 2; }
    #meta { height: auto; }
    #transcript { height: 1fr; }
    * { scrollbar-background: $background; scrollbar-background-hover: $background;
        scrollbar-background-active: $background; }
    #status { height: 1; padding: 0 1; color: $text-muted; background: $panel; }
    Prompt, Confirm, Help { align: center middle; }
    .dialog { width: 72; height: auto; padding: 1 2; border: round $accent; background: $surface; }
    .dialog Input { margin-top: 1; }
    Help .dialog { width: 96; max-height: 90%; overflow-y: auto; }
    Screen.-narrow #right { display: none; }
    Screen.-narrow #list { width: 1fr; max-width: 100%; }
    """
    BINDINGS = [
        Binding("slash", "filter", "Filter"),
        Binding("s", "search", "Search"),
        Binding("c", "close_session", "Close", show=False),
        Binding("o", "open('tab')", "New tab", show=False),
        Binding("O", "open('window')", "New window", show=False),
        Binding("n", "new", "New"),
        Binding("N", "new(True)", "New in worktree", show=False),
        Binding("f", "fork", "Fork", show=False),
        Binding("r", "rename", "Rename", show=False),
        Binding("x", "archive", "Archive", show=False),
        Binding("asterisk", "pin", "Pin"),
        Binding("space", "mark", "Mark", show=False),
        Binding("v", "toggle_flat", "Flat"),
        Binding("p", "toggle('pr')", "PRs", show=False),
        Binding("w", "toggle('worktree')", "Worktrees", show=False),
        Binding("l", "toggle('live')", "Live", show=False),
        Binding("exclamation_mark", "toggle('waiting')", "Waiting"),
        Binding("a", "toggle('archived')", "Archived"),
        Binding("e", "expand_all", "Expand", show=False),
        Binding("y", "copy_id", "Copy id", show=False),
        Binding("d", "trash", "Delete", show=False),
        Binding("E", "export", "Export", show=False),
        Binding("right_square_bracket", "jump(1)", "Next project", show=False),
        Binding("left_square_bracket", "jump(-1)", "Prev project", show=False),
        Binding("escape", "clear", "Clear", show=False),
        Binding("ctrl+r", "reload", "Reload", show=False),
        Binding("dollar_sign", "costs", "Costs"),
        Binding("W", "worktrees", "Worktrees", show=False),
        Binding("question_mark", "help", "Help"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, paths: data.Paths | None = None, focus_id: str | None = None,
                 host: tmux.Tmux | None = None, show_archived: bool = False, theme: str | None = None,
                 notifications: bool = True):
        super().__init__()
        self.theme_name = theme or default_theme()
        self.desktop: dict[str, data.DesktopRecord] = {}
        self.host = host  # set when running as the sidebar of a tmux window
        self.hosted: dict[str, str] = {}  # session id -> tmux pane, for sessions on our server
        self.shown_id: str | None = None
        self.paths = paths or data.Paths()
        self.focus_id = focus_id
        self.state = data.State(self.paths.state)
        self.sessions: list[Session] = []
        self.live: dict[str, LiveSession] = {}
        self.notifications = notifications
        self.waiting: set[str] = set()  # finished a turn since you last opened them; memory only
        self.last_status: dict[str, str] | None = None
        self.filters: set[str] = {"archived"} if show_archived else set()
        self.query_text = ""
        self.mode = "filter"  # what the search box is doing: "filter" or "search"
        self.search_query = ""
        self.hits: dict[str, list[str]] | None = None
        self.expanded: set[str] = set()
        self.marked: set[str] = set()  # session ids; in memory only
        self.by_id: dict[str, Session] = {}
        self.pending: dict[str, Pending] = {}
        self.loaded = False
        self.pr_status: dict[str, prs.PRStatus] = {}

    def compose(self) -> ComposeResult:
        yield Input(placeholder="/ to filter, s to search transcripts", id="search")
        with Horizontal(id="body"):
            yield SessionList(id="list")
            with Vertical(id="right"):
                yield Static(id="meta")
                with VerticalScroll(id="transcript"):
                    yield Static(id="messages")
        yield Static("Loading sessions…", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.theme = self.theme_name
        # Rich styles `inline code` "on black"; keep the terminal's background instead.
        self.console.push_theme(RichTheme({"markdown.code": "bold cyan"}))
        self.query_one("#search", Input).border_title = "filter"
        self.query_one(SessionList).focus()
        if self.host:
            self.poll_host()
        self.load()
        self.set_interval(2, self.poll_live)
        self.set_interval(20, self.load)
        self.set_interval(60, self.refresh_prs)

    # ---- loading ---------------------------------------------------------

    @work(thread=True, exclusive=True, group="load")
    def load(self) -> None:
        sessions = data.load_sessions(self.paths)
        live = data.load_live(self.paths)
        desktop = data.load_desktop(self.paths)
        self.call_from_thread(self.set_sessions, sessions, live, desktop)

    @work(thread=True, exclusive=True, group="prs")
    def refresh_prs(self) -> None:
        recent = sorted(self.sessions, key=lambda s: -s.mtime)
        urls = list(dict.fromkeys(s.pr_url for s in recent if s.pr_url))
        prs.refresh(self.paths, urls)
        self.call_from_thread(self.set_pr_status, prs.load(self.paths))

    def set_pr_status(self, status: dict[str, prs.PRStatus]) -> None:
        if status != self.pr_status:
            self.pr_status = status
            self.rebuild()

    def set_sessions(self, sessions: list[Session], live: dict[str, LiveSession],
                     desktop: dict[str, data.DesktopRecord]) -> None:
        changed = ([(s.id, s.mtime, s.title) for s in sessions] != [(s.id, s.mtime, s.title) for s in self.sessions]
                   or desktop != self.desktop)
        self.track(live)
        self.sessions, self.live, self.desktop = sessions, live, desktop
        if changed or not self.loaded:
            self.loaded = True
            self.rebuild()
            self.refresh_prs()

    def poll_live(self) -> None:
        live = data.load_live(self.paths)
        changed = self.host is not None and self.poll_host()
        before = set(self.waiting)
        self.track(live)
        if (changed or self.waiting != before
                or {k: v.status for k, v in live.items()} != {k: v.status for k, v in self.live.items()}):
            self.live = live
            self.rebuild()

    def track(self, live: dict[str, LiveSession]) -> None:
        """Mark sessions that went from working to idle as waiting. The first call has no history."""
        prev, self.last_status = self.last_status, {k: v.status for k, v in live.items()}
        self.waiting &= {k for k, v in live.items() if v.status == "idle"}
        for sid, now in self.last_status.items():
            if prev is None or now != "idle" or prev.get(sid, "idle") == "idle":
                continue
            self.waiting.add(sid)
            if self.notifications and sid != self.shown_id:
                title = next((x.title for x in self.sessions if x.id == sid), None) or live[sid].name or sid[:8]
                self.send_notification(f"{title} is waiting")

    @work(thread=True, group="notify")
    def send_notification(self, message: str) -> None:
        notify.send(message)  # may wait on tmux; keep it off the UI thread

    def poll_host(self) -> bool:
        """Refresh which sessions run on our tmux server. Returns True if anything changed."""
        try:
            hosted, shown = self.host.hosted(), self.host.shown()
        except (subprocess.CalledProcessError, OSError):
            return False
        changed = (hosted, shown) != (self.hosted, self.shown_id)
        self.hosted, self.shown_id = hosted, shown
        return changed


    # ---- list ------------------------------------------------------------

    @property
    def narrowed(self) -> bool:
        return bool(self.query_text.strip() or self.hits is not None or self.filters - {"archived"})

    def visible(self) -> list[Session]:
        tokens = self.query_text.lower().split()
        out: list[Session] = []
        for s in self.sessions:  # newest first, so projects come out ordered by recent activity
            if self.archived_by(s) and "archived" not in self.filters:
                continue
            if "pr" in self.filters and not s.pr_number:
                continue
            if "worktree" in self.filters and not s.worktree:
                continue
            if "live" in self.filters and s.id not in self.live:
                continue
            if "waiting" in self.filters and s.id not in self.waiting:
                continue
            if self.hits is not None and s.id not in self.hits:
                continue
            haystack = f"{s.project_name} {s.title} {s.branch or ''}".lower()
            if any(t not in haystack for t in tokens):
                continue
            out.append(s)
        return out

    @staticmethod
    def grouped(sessions: list[Session]) -> dict[str, list[Session]]:
        out: dict[str, list[Session]] = {}
        for s in sessions:
            out.setdefault(s.project, []).append(s)
        return out

    def archived_by(self, s: Session) -> str | None:
        if s.id in self.state.archived:
            return "csm"
        if (rec := self.desktop.get(s.id)) and rec.archived:
            return "Claude desktop"
        return None

    def pending_groups(self) -> dict[str, list[Pending]]:
        known = {s.id for s in self.sessions}
        self.pending = {k: p for k, p in self.pending.items() if k not in known and k in self.hosted}
        out: dict[str, list[Pending]] = {}
        for p in reversed(self.pending.values()):
            out.setdefault(p.project, []).append(p)
        return out if not self.narrowed else {}

    def pending_row(self, p: Pending) -> Text:
        dot = "▶ " if p.id == self.shown_id else "● "
        return Text.assemble((dot, "cyan"), ("○ ", "dim"), (p.launch.label, "italic"))

    def tags(self, s: Session, with_project: bool) -> tuple[Text, Text]:
        """Leading mark column (only while something is marked) and trailing project name."""
        mark = Text("✓ " if s.id in self.marked else "  ", style="bold green") if self.marked else Text()
        return mark, Text(f"  {s.project_name}", style="dim") if with_project else Text()

    def row(self, s: Session, with_project: bool = False) -> Text:
        mark, project = self.tags(s, with_project)
        live = self.live.get(s.id)
        style = STATUS_STYLE.get(live.status, "cyan") if live else ""
        dot = self.marker(s, live, style)
        icon = ("⇄ ", pr_style(self.pr_status.get(s.pr_url or ""))) if s.pr_number else ("⑂ ", "magenta") if s.worktree else ("○ ", "dim")
        title = "dim italic" if self.archived_by(s) else "bold" if s.id in self.waiting else ""
        return Text.assemble(mark, dot, icon, (s.title, title), project)

    def marker(self, s: Session, live: LiveSession | None, style: str) -> tuple[str, str]:
        if s.id == self.shown_id:
            return "▶ ", style
        if s.id in self.waiting:
            return "◆ ", "bold " + style
        return ("● " if live else "  "), style

    def rebuild(self) -> None:
        lst = self.query_one(SessionList)
        current = lst.highlighted_option.id if lst.highlighted_option else None
        keep = f"s:{self.focus_id}" if self.focus_id else current
        self.focus_id = None
        visible = self.visible()
        pending = self.pending_groups()
        pinned = [s for s in visible if s.id in self.state.pinned]
        rest = [s for s in visible if s.id not in self.state.pinned]
        # (key, header, sessions, per-group limit, rows show their project)
        sections = [(PINNED, "Pinned", pinned, None, True)] if pinned else []
        if self.state.flat:
            sections += [("", "", rest, None, True)]
        else:
            groups = self.grouped(rest)
            groups = {**{k: [] for k in pending if k not in groups}, **groups}  # projects with only new sessions
            sections += [(p, os.path.basename(p) or p, ss, PER_PROJECT, False) for p, ss in groups.items()]

        def pending_for(key: str) -> list[Pending]:
            if key == PINNED:
                return []
            return [p for ps in pending.values() for p in ps] if key == "" else pending.get(key, [])
        options: list[Option | None] = []
        self.by_id = {}
        for project, name, sessions, cap, with_project in sections:
            new = pending_for(project)
            if not sessions and not new:
                continue
            collapsed = bool(name) and project in self.state.collapsed and not self.narrowed
            limit = None if self.narrowed or project in self.expanded else cap
            shown = [] if collapsed else sessions[:limit]
            if options:
                options.append(Option("", disabled=True))
            if name:
                options.append(Option(Text.assemble(
                    ("▸ " if collapsed else "▾ ", "dim"), (name, "bold"), (f"  {len(sessions) + len(new)}", "dim")),
                    id=f"p:{project}"))
            for p in [] if collapsed else new:
                options.append(Option(self.pending_row(p), id=f"s:{p.id}"))
            for s in shown:
                self.by_id[s.id] = s
                options.append(Option(self.row(s, with_project), id=f"s:{s.id}"))
            if len(shown) < len(sessions) and not collapsed:
                options.append(Option(Text(f"    … {len(sessions) - len(shown)} more", style="dim italic"),
                                      id=f"m:{project}"))
        lst.clear_options()
        lst.add_options(options)
        ids = [o.id for o in lst.options]
        if keep and keep in ids:
            lst.highlighted = ids.index(keep)
        elif ids:
            lst.highlighted = next((i for i, x in enumerate(ids) if x and x.startswith("s:")), 0)
        else:
            self.show(None)
        self.update_status(len(visible))

    def update_status(self, count: int) -> None:
        parts = [f"{count} sessions", f"{len(self.live)} live"]
        if self.waiting:
            parts.append(f"{len(self.waiting)} waiting")
        if only := sorted(self.filters - {"archived"}):
            parts.append("only " + ", ".join(FILTER_NAMES[f] for f in only))
        if "archived" in self.filters:
            parts.append("including archived")
        if self.query_text.strip():
            parts.append(f"filter “{self.query_text.strip()}”")
        if self.marked:
            parts.append(f"{len(self.marked)} marked")
        if self.hits is not None:
            parts.append(f"text “{self.search_query}”: {len(self.hits)} sessions")
        self.query_one("#status", Static).update("  ·  ".join(parts) + "    [dim]? help[/]")

    def selected(self) -> Session | None:
        opt = self.query_one(SessionList).highlighted_option
        return self.by_id.get(opt.id[2:]) if opt and opt.id and opt.id.startswith("s:") else None

    @on(OptionList.OptionHighlighted)
    def highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self.show(self.selected())

    @on(OptionList.OptionSelected)
    def chosen(self, event: OptionList.OptionSelected) -> None:
        kind, key = event.option.id[:2], event.option.id[2:]
        if kind == "p:":
            self.state.collapsed ^= {key}
            self.state.save()
            self.rebuild()
        elif kind == "m:":
            self.expanded.add(key)
            self.rebuild()
        elif p := self.pending.get(key):
            self.start_launch(p.launch)
        elif s := self.by_id.get(key):
            self.resume(s)

    # ---- preview ---------------------------------------------------------

    def show(self, s: Session | None) -> None:
        meta = self.query_one("#meta", Static)
        if s is None:
            meta.update(Text("No session selected", style="dim"))
            self.query_one("#messages", Static).update("")
            return
        meta.update(self.meta(s))
        self.load_preview(s)

    def meta(self, s: Session) -> Group:
        lines = [Text(s.title, style="bold")]
        info = [s.project_name]
        if s.branch:
            info.append(s.branch)
        info.append(ago(s.mtime))
        if s.started:
            try:
                info.append("started " + datetime.fromisoformat(s.started.replace("Z", "+00:00"))
                            .astimezone().strftime("%b %-d %-I:%M %p"))
            except ValueError:
                pass
        if s.cost:
            info.append(f"${s.cost:,.2f}")
        lines.append(Text("  ·  ".join(info), style="dim"))
        if live := self.live.get(s.id):
            lines.append(Text.assemble(("● ", STATUS_STYLE.get(live.status, "cyan")),
                                       f"{live.status} in {live.entrypoint or 'claude'} (pid {live.pid})"))
        if s.id in self.waiting:
            lines.append(Text("◆ waiting for you", style="bold"))
        if s.pr_url:
            st = self.pr_status.get(s.pr_url)
            lines.append(Text.assemble(("⇄ ", pr_style(st)), (f"#{s.pr_number} ", "bold"),
                                       pr_summary(st) + "  " if st else "", (s.pr_url, "dim")))
        if by := self.archived_by(s):
            lines.append(Text(f"archived in {by}", style="italic yellow"))
        lines.append(Text(f"{tilde(s.cwd)}  ·  {s.id}", style="dim"))
        if self.hits and s.id in self.hits:
            lines.append(Text(""))
            for snip in self.hits[s.id]:
                t = Text("  " + snip, style="italic")
                t.highlight_words([self.search_query], "reverse", case_sensitive=False)
                lines.append(t)
        lines.append(Rule(style="dim"))
        return Group(*lines)

    @work(thread=True, exclusive=True, group="preview")
    def load_preview(self, s: Session) -> None:
        try:
            messages = data.transcript(s.path)
        except OSError:
            messages = []
        if not get_current_worker().is_cancelled:
            self.call_from_thread(self.show_messages, s, messages)

    def show_messages(self, s: Session, messages: list[Message]) -> None:
        if (cur := self.selected()) is None or cur.id != s.id:
            return
        # Markdown normally; plain text while a transcript search is active, so matches can be highlighted.
        searching = bool(self.search_query and self.hits is not None)
        parts = []
        for m in messages:
            text = m.text if len(m.text) <= MAX_MESSAGE_CHARS else m.text[:MAX_MESSAGE_CHARS] + " …"
            if m.role == "tools":
                n = int(m.text)
                parts.append(Text(f"  ran {n} tool call{'s' if n != 1 else ''}", style="dim"))
            else:
                user = m.role == "user"
                if searching:
                    body = Text(text, style="bold" if user else "")
                    body.highlight_words([self.search_query], "reverse", case_sensitive=False)
                else:
                    # ANSI code themes have no background, so code blocks sit on the terminal's.
                    code_theme = "ansi_dark" if self.current_theme.dark else "ansi_light"
                    body = Markdown(text, style="bold" if user else "none", code_theme=code_theme)
                row = Table.grid(expand=True)
                row.add_column(width=2)
                row.add_column(ratio=1)
                row.add_row(Text("❯", style="bold cyan") if user else Text("●", style="dim"), body)
                parts.append(row)
            parts.append(Text(""))
        self.query_one("#messages", Static).update(Group(*parts) if parts else Text("(no messages)", style="dim"))
        self.query_one("#transcript", VerticalScroll).scroll_end(animate=False)

    # ---- search box ------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        box = self.query_one("#search", Input)
        self.mode = mode
        box.border_title = "filter" if mode == "filter" else "search transcripts (enter)"
        box.value = self.query_text if mode == "filter" else self.search_query
        box.focus()

    def action_filter(self) -> None:
        self.set_mode("filter")

    def action_search(self) -> None:
        self.set_mode("search")

    @on(Input.Changed, "#search")
    def typed(self, event: Input.Changed) -> None:
        if self.mode == "filter" and event.value != self.query_text:
            self.query_text = event.value
            self.rebuild()

    @on(Input.Submitted, "#search")
    def submitted(self, event: Input.Submitted) -> None:
        if self.mode == "search":
            q = event.value.strip()
            if not q:
                self.hits, self.search_query = None, ""
                self.rebuild()
            else:
                self.search_query = q
                self.query_one("#status", Static).update(f"searching transcripts for “{q}”…")
                self.run_search(q)
        self.query_one(SessionList).focus()

    @work(thread=True, exclusive=True, group="search")
    def run_search(self, q: str) -> None:
        worker = get_current_worker()
        hits = data.search(self.sessions, q, cancelled=lambda: worker.is_cancelled)
        if not worker.is_cancelled:
            self.call_from_thread(self.apply_search, hits)

    def apply_search(self, hits: dict[str, list[str]]) -> None:
        self.hits = hits
        self.rebuild()

    def action_clear(self) -> None:
        box = self.query_one("#search", Input)
        self.query_text, self.search_query, self.hits = "", "", None
        self.mode = "filter"
        self.marked.clear()
        box.border_title = "filter"
        box.value = ""
        self.query_one(SessionList).focus()
        self.rebuild()

    # ---- filters and navigation -------------------------------------------

    def action_toggle(self, name: str) -> None:
        self.filters ^= {name}
        self.rebuild()

    def action_expand_all(self) -> None:
        projects = {s.project for s in self.sessions}
        self.expanded = set() if self.expanded >= projects else projects
        self.state.collapsed.clear()
        self.state.save()
        self.rebuild()

    def action_jump(self, step: int) -> None:
        lst = self.query_one(SessionList)
        heads = [i for i, o in enumerate(lst.options) if o.id and o.id.startswith("p:")]
        if not heads or self.state.flat and len(heads) == 1:
            return
        cur = lst.highlighted or 0
        ahead = [i for i in heads if (i > cur if step > 0 else i < cur)]
        target = (ahead[0] if step > 0 else ahead[-1]) if ahead else (heads[0] if step > 0 else heads[-1])
        # Land on the project's first session rather than its header, when it has one.
        nxt = target + 1
        if nxt < len(lst.options) and (lst.options[nxt].id or "").startswith("s:"):
            target = nxt
        lst.highlighted = target

    def action_reload(self) -> None:
        self.load()
        self.notify("Reloading…", timeout=1)

    def action_costs(self) -> None:
        self.push_screen(Costs(self.sessions, self.archived_by, ago))

    def action_worktrees(self) -> None:
        self.push_screen(Worktrees(self.paths, self.sessions, self.live, Confirm))

    def action_help(self) -> None:
        self.push_screen(Help())

    # ---- actions on a session --------------------------------------------

    def resume(self, s: Session) -> None:
        if s.id in self.waiting:
            self.waiting.discard(s.id)
            self.rebuild()
        self.resume_flow(s)

    @work(thread=True, exclusive=True, group="resume")
    def resume_flow(self, s: Session) -> None:
        if s.id in self.hosted:
            self.call_from_thread(self.start, s)
            return
        live = self.live.get(s.id)
        if live and ghostty.running():
            try:
                term = ghostty.find(s, live, ghostty.terminals())
                if term and ghostty.focus(term.id):
                    self.call_from_thread(self.notify, f"Switched to “{term.title}”", timeout=2)
                    return
            except RuntimeError:
                pass
        if live and desktop.can_open(live):
            try:
                desktop.open_session(live)
                self.call_from_thread(self.notify, "Opened in Claude desktop", timeout=2)
                return
            except (OSError, subprocess.CalledProcessError):
                pass
        if live:
            msg = (f"“{s.title}” is already open in {live.entrypoint or 'claude'} (pid {live.pid}).\n"
                   "Resuming it a second time means two processes writing one transcript. Resume anyway?")
            self.call_from_thread(self.push_screen, Confirm(msg), lambda yes: yes and self.start(s))
        else:
            self.call_from_thread(self.start, s)

    def start(self, s: Session) -> None:
        self.start_launch(launch.resume(s.id, s.cwd, s.title))

    def start_launch(self, l: Launch) -> None:
        self.waiting.discard(l.focus_id)
        if not self.host:
            self.exit(l)
            return
        try:
            self.host.show(l.focus_id, l.cwd, l.command, l.label[:40])
        except subprocess.CalledProcessError as e:
            self.notify(f"tmux: {(e.stderr or '').strip() or e}", severity="error")
        self.poll_host()
        self.rebuild()

    def current_project(self) -> str | None:
        opt = self.query_one(SessionList).highlighted_option
        if not opt or not opt.id:
            return None
        kind, key = opt.id[:2], opt.id[2:]
        if kind in ("p:", "m:"):
            return key
        p = self.pending.get(key)
        s = self.by_id.get(key)
        return p.project if p else s.project if s else None

    def begin(self, l: Launch, project: str) -> None:
        if not os.path.isdir(l.cwd):
            self.notify(f"{tilde(l.cwd)} no longer exists", severity="error")
            return
        if self.host:
            self.pending[l.focus_id] = Pending(l.focus_id, project, l)
            self.focus_id = l.focus_id
        self.start_launch(l)

    def action_new(self, worktree: bool = False) -> None:
        if project := self.current_project():
            self.begin(launch.new(project, worktree), project)

    def action_fork(self) -> None:
        if s := self.selected():
            self.begin(launch.fork(s.id, s.cwd, s.title), s.project)

    def action_close_session(self) -> None:
        if not (s := self.selected()):
            return
        if not self.host or s.id not in self.hosted:
            self.notify("Only sessions opened here can be closed here", severity="warning")
            return

        def done(yes: bool) -> None:
            if yes:
                self.host.close(s.id)
                self.poll_host()
                self.rebuild()
        live = self.live.get(s.id)
        if live and live.status != "idle":
            self.push_screen(Confirm(f"“{s.title}” is {live.status}. Stop it?"), done)
        else:
            done(True)

    async def action_quit(self) -> None:
        if self.host and self.host.own:
            self.host.detach()  # sessions keep running; `csm` reattaches
        else:
            self.exit()

    def action_open(self, where: str) -> None:
        if not (s := self.selected()):
            return
        if live := self.live.get(s.id):
            msg = (f"“{s.title}” is already open in {live.entrypoint or 'claude'} (pid {live.pid}).\n"
                   f"Open it in a new {where} anyway?")
            self.push_screen(Confirm(msg), lambda yes: yes and self.open_in_ghostty(s, where))
        else:
            self.open_in_ghostty(s, where)

    @work(thread=True, group="ghostty")
    def open_in_ghostty(self, s: Session, where: str) -> None:
        # AppleScript drives the running Ghostty. `open -na Ghostty.app` would start a
        # second instance, which restores every saved tab alongside the new one.
        try:
            ghostty.open_session(s.cwd, launch.resume(s.id, s.cwd).command, where)
        except RuntimeError as e:
            self.call_from_thread(self.notify, f"Ghostty: {e}", severity="error")
        else:
            self.call_from_thread(self.notify, f"Opened “{s.title}” in a new {where}", timeout=2)

    def action_rename(self) -> None:
        if not (s := self.selected()):
            return

        def done(title: str | None) -> None:
            if title and title != s.title:
                data.rename(s, title)
                s.title = title
                self.rebuild()
                self.show(s)
        self.push_screen(Prompt("Rename session", s.title), done)

    def action_pin(self) -> None:
        if not (s := self.selected()):
            return
        self.state.pinned ^= {s.id}
        self.state.save()
        self.focus_id = s.id
        self.rebuild()

    def action_toggle_flat(self) -> None:
        self.state.flat = not self.state.flat
        self.state.save()
        self.rebuild()

    def action_mark(self) -> None:
        if not (s := self.selected()):
            return
        self.marked ^= {s.id}
        self.rebuild()
        lst = self.query_one(SessionList)
        after = (i for i, o in enumerate(lst.options) if i > (lst.highlighted or 0) and (o.id or "").startswith("s:"))
        if (nxt := next(after, None)) is not None:
            lst.highlighted = nxt

    def targets(self) -> list[Session]:
        """The marked sessions still in the list, or else the highlighted one."""
        marked = [s for s in self.by_id.values() if s.id in self.marked]
        return marked or ([s] if (s := self.selected()) else [])

    def action_export(self) -> None:
        if targets := self.targets():
            self.notify("Exporting…", timeout=2)
            self.run_export(targets)

    @work(thread=True, group="export")
    def run_export(self, targets: list[Session]) -> None:
        try:
            files = [export.export(s, self.paths.export) for s in targets]
        except OSError as e:
            self.call_from_thread(self.notify, f"Export failed: {e}", severity="error")
            return
        what = tilde(str(files[0])) if len(files) == 1 else f"{len(files)} files in {tilde(str(self.paths.export))}"
        self.call_from_thread(self.notify, f"Exported to {what}", timeout=4)
        export.reveal(files)

    def action_archive(self) -> None:
        targets = self.targets()
        if not targets:
            return
        if len(targets) == 1 and (s := targets[0]).id not in self.state.archived and self.archived_by(s):
            self.notify("Archived in Claude desktop; unarchive it there", severity="warning")
            return
        targets = [s for s in targets if s.id in self.state.archived or not self.archived_by(s)]
        archiving = any(s.id not in self.state.archived for s in targets)
        ids = {s.id for s in targets}
        self.state.archived = self.state.archived | ids if archiving else self.state.archived - ids
        self.state.save()
        what = f"“{targets[0].title}”" if len(targets) == 1 else f"{len(targets)} sessions"
        self.notify(f"{'Archived' if archiving else 'Unarchived'} {what}", timeout=2)
        self.marked.clear()
        self.rebuild()

    def action_copy_id(self) -> None:
        if not (s := self.selected()):
            return
        try:
            subprocess.run(["pbcopy"], input=s.id.encode(), check=True)
        except (OSError, subprocess.CalledProcessError):
            self.copy_to_clipboard(s.id)
        self.notify(f"Copied {s.id}", timeout=2)

    def action_trash(self) -> None:
        every = self.targets()
        targets = [s for s in every if s.id not in self.live]
        if skipped := len(every) - len(targets):
            self.notify(f"{skipped} running session{'s' if skipped != 1 else ''} skipped; close "
                        f"{'them' if skipped != 1 else 'it'} first", severity="warning")
        if not targets:
            return

        def done(yes: bool) -> None:
            if yes:
                for s in targets:
                    dest = data.trash(s, self.paths)
                self.notify(f"Moved to {tilde(str(dest))}" if len(targets) == 1
                            else f"Moved {len(targets)} sessions to the Trash", timeout=3)
                self.marked.clear()
                self.load()
        what = f"“{targets[0].title}”" if len(targets) == 1 else f"{len(targets)} sessions"
        self.push_screen(Confirm(f"Move {what} to the Trash?"), done)


def main() -> None:
    ap = argparse.ArgumentParser(prog="csm", description=__doc__)
    ap.add_argument("--no-tmux", action="store_true",
                    help="don't use tmux; resume sessions in this terminal and return to the list after")
    ap.add_argument("--once", action="store_true", help="with --no-tmux, exit after resuming")
    ap.add_argument("--archived", action="store_true", help="start with archived sessions shown")
    ap.add_argument("--no-notify", action="store_true", help="don't send a desktop notification when a session is waiting")
    ap.add_argument("--export-dir", help="where E writes Markdown exports; default ~/Downloads/claude-sessions. "
                                         "Also CSM_EXPORT_DIR.")
    ap.add_argument("--theme", help="Textual theme; default ansi-light or ansi-dark (follows macOS), "
                                    "which use the terminal's own colors and background. Also CSM_THEME.")
    ap.add_argument("--sidebar", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.export_dir:
        os.environ["CSM_EXPORT_DIR"] = os.path.abspath(os.path.expanduser(args.export_dir))
    if args.sidebar or (os.environ.get("TMUX") and not args.no_tmux):
        host = tmux.Tmux(own=args.sidebar)
        host.mark_sidebar()
        CSM(host=host, show_archived=args.archived, theme=args.theme, notifications=not args.no_notify).run()
        return
    if not args.no_tmux and tmux.available():
        extra = (["--archived"] if args.archived else []) + (["--no-notify"] if args.no_notify else []) + (["--theme", args.theme] if args.theme else [])
        extra += ["--export-dir", os.environ["CSM_EXPORT_DIR"]] if args.export_dir else []
        tmux.launch(extra)  # does not return
    focus = None
    while True:
        l = CSM(focus_id=focus, show_archived=args.archived, theme=args.theme,
                notifications=not args.no_notify).run()
        if l is None:
            return
        print(f"\n\033[2mstarting\033[0m {l.label}  \033[2m({tilde(l.cwd)})\033[0m\n", flush=True)
        # A handler (unlike SIG_IGN) resets to the default on exec, so claude still gets ^C
        # while we survive it and can bring the list back.
        previous = signal.signal(signal.SIGINT, lambda *_: None)
        try:
            subprocess.run(l.argv, cwd=l.cwd if os.path.isdir(l.cwd) else None)
        finally:
            signal.signal(signal.SIGINT, previous)
        if args.once:
            return
        focus = l.focus_id


if __name__ == "__main__":
    main()
