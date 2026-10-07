"""csm — browse, search and resume Claude Code sessions."""
from __future__ import annotations

import argparse
from collections import Counter
import os
import signal
import subprocess
import sys
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
from textual.suggester import Suggester
from textual.widgets import Footer, Input, Label, OptionList, Static, Switch
from textual.widgets.option_list import Option
from textual.worker import get_current_worker

from . import autoarchive, data, desktop, export, ghostty, hooks, launch, links, notify, prs, summary, tmux, worktrees
from .activity import Activity
from .costs import Costs
from .data import LiveSession, Message, Session, normalize_tags
from .launch import Launch, Pending
from .viewer import Viewer
from .loadout import LoadoutView
from .worktrees import Worktrees

PER_PROJECT = 5
PINNED = "__pinned__"  # pseudo-project key for the Pinned group
PULSE = 0.8  # seconds per half of a working session's dot pulse
WARN_ON_LIGHT = "#9a6700"  # dark amber: readable on light backgrounds
STATUS_STYLE = {"idle": "green", "busy": "warn"}
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
  S         summary of the last 48 hours: questions waiting on you, decisions, finished work
  e         show every session (5 per project otherwise)
  v         flat newest-first list / grouped by project

[b]Find[/b]
  /         filter by title, project, branch or note as you type; #tag matches tags
  s         search transcript text (enter runs it)
  esc       clear the filter, search and marks
  p w l !   only PR-linked / worktree / live / waiting-for-you sessions
  a         also show archived sessions (by csm, Claude desktop or the auto rule)

[b]Open[/b]
  enter     beside this list in tmux, else here (back to the list when claude exits).
            A session already open in a Ghostty tab or Claude desktop is shown there.
  ctrl+\\    cycle focus: this list, then the sessions beside it top to bottom (tmux);
            with none open, switch between this list and the preview
  n / N     new session in the highlighted project / in a new worktree
  P         new session in any directory (tab completes; offers to create a missing one)
  tab       open the session that has waited longest for you (permission first); shift+tab goes back
  |         (tmux) show the highlighted session as a second pane; enter goes back to one
  R         (tmux) reply to the highlighted session without opening it
  f         fork the highlighted session
  o / O     resume in a new Ghostty tab / window
  c         (tmux) stop the session's claude process
  g         open the session's PR in the browser
  .         open the project in your editor ($CSM_EDITOR, code, cursor, or Finder)
  D         open the session in Claude desktop, even if it isn't running there

[b]Manage[/b]
  r         rename                        y         copy session id
  x         archive / unarchive           d         move transcript to the Trash
  X         retire: stop it (csm pane or background job) and archive (marked, or highlighted)
  @         when it goes idle: notify me, archive, retire, or (tmux) send it a prompt
  A         auto-archive rule (merged PRs, idle sessions); x keeps an auto-archived one
  E         export to Markdown (marked, or highlighted)
  #         edit tags (#waiting-on-chris; marked sessions get them added)
  i         edit a note; both show in the preview and are searched by /  (#tag)
  t         read the whole transcript (/ search, n N next / previous, g G top / bottom)
  L         what the session loaded: plugins, skills (✓ used), MCP servers, agents, hooks, CLAUDE.md files
  *         pin / unpin                   space     mark; x and d act on all marked
  $         costs                         W         clean up worktrees
  ctrl+r    reload
  q         quit (in tmux: detach; sessions keep running)

[b]Icons[/b]
  [green]⇄[/] PR linked: [warn]pending[/], [red]failing[/], [magenta]merged[/], [dim]closed / draft[/]    [magenta]⑂[/] worktree   [dim]○[/] other
  [green]●[/] live, idle   [warn]●[/] live, busy   » shown beside the list
  on a project:  [warn]±3[/] uncommitted files   [cyan]↑2[/] commits to push   [dim]↓1[/] to pull
  ◆ waiting for you   [bold red]?[/] needs permission (with hooks); both show how long, ◆ 12m   ⑃ fork   [red]◔[/] context over 80%
  bg  a background job   [dim]⇢[/] a terminal attached to a background job (enter on the job shows it)
"""


def git_flags(st: tuple[int, int, int] | None) -> Text:
    """±uncommitted files, ↑commits to push, ↓commits to pull, after a project's name."""
    if not st:
        return Text()
    dirty, ahead, behind = st
    return Text.assemble(*[("  " + t, style) for t, style, n in
                           ((f"±{dirty}", "warn", dirty), (f"↑{ahead}", "cyan", ahead), (f"↓{behind}", "dim", behind)) if n])


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
        return "warn"
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


def waited(ts: float) -> str:
    """How long since ts, compactly: 1m, 25m, 3h, 2d."""
    d = time.time() - ts
    for unit, sec in (("d", 86400), ("h", 3600)):
        if d >= sec:
            return f"{int(d // sec)}{unit}"
    return f"{max(1, int(d // 60))}m"


def context_fraction(s: Session) -> float | None:
    if not s.context_tokens:
        return None
    return s.context_tokens / data.context_window(s.context_model, s.context_tokens)


def context_style(frac: float) -> str:
    return "red" if frac > 0.8 else "warn" if frac >= 0.5 else "dim"


def context_flag(s: Session) -> Text:
    """A small red mark after the title once the context is nearly full."""
    frac = context_fraction(s)
    return Text(" ◔", style="red") if frac is not None and frac > 0.8 else Text()


def context_line(s: Session) -> Text | None:
    frac = context_fraction(s)
    if frac is None:
        return None
    window = data.context_window(s.context_model, s.context_tokens)
    filled = min(10, int(frac * 10))
    return Text(f"context {'▰' * filled}{'▱' * (10 - filled)} {frac:.0%} · "
                f"{s.context_tokens // 1000}k of {'1M' if window >= 1_000_000 else f'{window // 1000}k'} tokens",
                style=context_style(frac))


def tilde(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home) else path


class SessionList(OptionList):
    BINDINGS = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        # Here rather than on the app, so tab still completes paths in dialogs.
        Binding("tab", "app.next_waiting", "Next waiting", show=False),
        Binding("shift+tab", "app.next_waiting(True)", show=False),
    ]
    # Rows of sessions open beside the list get a soft background across the whole row.
    COMPONENT_CLASSES = {"session-list--open"}
    DEFAULT_CSS = """
    /* Fixed greys: the ansi themes' colors come from the terminal and can't be mixed into a tint. */
    SessionList:light > .session-list--open { background: #eeeeee; }
    SessionList:dark > .session-list--open { background: #333333; }
    SessionList:light > .option-list--option-hover { background: #e6e6e6; }
    SessionList:dark > .option-list--option-hover { background: #3a3a3a; }
    /* Away in a session pane, the cursor fades to a grey; the list's own focus keeps it solid. */
    SessionList:blur > .option-list--option-highlighted { color: $foreground; text-style: none; }
    SessionList:light:blur > .option-list--option-highlighted { background: #dcdcdc; }
    SessionList:dark:blur > .option-list--option-highlighted { background: #444444; }
    """
    open_ids: set[str] = set()

    def _get_option_render(self, option: Option, style):
        index = self._option_to_index.get(option)
        if option.id in self.open_ids and index != self.highlighted and index != self._mouse_hovering_over:
            style = self.get_visual_style("option-list--option", "session-list--open")
        return super()._get_option_render(option, style)


class Prompt(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(self, title: str, value: str = "", allow_empty: bool = False):
        super().__init__()
        self.title_text, self.value, self.allow_empty = title, value, allow_empty

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text)
            yield Input(value=self.value, select_on_focus=True)

    @on(Input.Submitted)
    def submit(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self.dismiss(text if text or self.allow_empty else None)

    def action_cancel(self) -> None:
        self.dismiss(None)


def complete_dir(value: str) -> str:
    """Extend a typed path as far as the directories on disk agree, adding / once it's unique."""
    parent, prefix = os.path.split(os.path.expanduser(value))
    try:
        names = sorted(e.name for e in os.scandir(parent or ".") if e.is_dir() and e.name.startswith(prefix)
                       and (prefix.startswith(".") or not e.name.startswith(".")))
    except OSError:
        return value
    if not names:
        return value
    common = os.path.commonprefix(names)
    return value + common[len(prefix):] + ("/" if len(names) == 1 else "")


class DirSuggester(Suggester):
    def __init__(self):
        super().__init__(use_cache=False)

    async def get_suggestion(self, value: str) -> str | None:
        done = complete_dir(value)
        return done if done != value else None


class DirPrompt(ModalScreen[str | None]):
    """Ask for a directory; tab (or →) takes the grey completion."""
    BINDINGS = [Binding("escape", "cancel", show=False), Binding("tab", "complete", show=False)]

    def __init__(self, title: str, value: str):
        super().__init__()
        self.title_text, self.value = title, value

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text)
            yield Input(value=self.value, suggester=DirSuggester())
            yield Label("[dim]tab = complete    enter = start    esc = cancel[/]")

    def action_complete(self) -> None:
        box = self.query_one(Input)
        box.value = complete_dir(box.value)
        box.cursor_position = len(box.value)

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


class WhenIdle(ModalScreen[str | None]):
    """Pick what to do once a session goes idle: one key per choice."""
    BINDINGS = [Binding("n", "pick('notify')", show=False), Binding("a", "pick('archive')", show=False),
                Binding("r", "pick('retire')", show=False), Binding("s", "pick('send')", show=False),
                Binding("c", "pick('cancel')", show=False), Binding("escape", "pick", show=False)]

    def __init__(self, title: str, queued: str | None, can_send: bool):
        super().__init__()
        self.title_text, self.queued, self.can_send = title, queued, can_send

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(f"When “{self.title_text}” goes idle:")
            yield Label("  n  notify me\n  a  archive it\n  r  retire it (stop and archive)"
                        + ("\n  s  send it a prompt" if self.can_send else ""))
            if self.queued:
                yield Label(f"  c  cancel the queued “{self.queued}”")
            yield Label("[dim]esc = close[/]")

    def action_pick(self, choice: str | None = None) -> None:
        if choice == "send" and not self.can_send or choice == "cancel" and not self.queued:
            return
        self.dismiss(choice)


class AutoArchiveSettings(ModalScreen[dict | None]):
    BINDINGS = [Binding("escape", "cancel", show=False), Binding("ctrl+s", "save", show=False)]

    def __init__(self, rule: dict):
        super().__init__()
        self.rule = rule

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label("Auto-archive (never live, pinned or kept sessions)")
            with Horizontal(classes="row"):
                yield Switch(self.rule["enabled"], id="enabled")
                yield Label("enabled")
            yield Label("PR merged more than this many days ago")
            yield Input(str(self.rule["merged_days"]), type="integer", id="merged_days")
            yield Label("no activity for this many days")
            yield Input(str(self.rule["idle_days"]), type="integer", id="idle_days")
            yield Label("[dim]enter / ctrl+s = save    esc = cancel[/]")

    @on(Input.Submitted)
    def action_save(self) -> None:
        self.dismiss(autoarchive.normalize({
            "enabled": self.query_one("#enabled", Switch).value,
            **{k: int(v) if (v := self.query_one(f"#{k}", Input).value.strip()).isdigit() else None
               for k in ("merged_days", "idle_days")}}))

    def action_cancel(self) -> None:
        self.dismiss(None)


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
    #activity { height: 1; padding: 0 1; }
    #status { height: 1; padding: 0 1; color: $text-muted; background: $panel; }
    Prompt, Confirm, Help, AutoArchiveSettings { align: center middle; }
    .dialog .row { height: auto; }
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
        Binding("X", "retire", "Retire", show=False),
        Binding("at", "when_idle", "When idle", show=False),
        Binding("L", "loadout", "Loaded", show=False),
        Binding("o", "open('tab')", "New tab", show=False),
        Binding("O", "open('window')", "New window", show=False),
        Binding("n", "new", "New"),
        Binding("N", "new(True)", "New in worktree", show=False),
        Binding("P", "new_project", "New in a directory", show=False),
        Binding("R", "reply", "Reply", show=False),
        Binding("vertical_line", "open_also", "Side by side", show=False),
        Binding("f", "fork", "Fork", show=False),
        Binding("r", "rename", "Rename", show=False),
        Binding("number_sign", "tag", "Tags", show=False),
        Binding("i", "note", "Note", show=False),
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
        Binding("g", "open_pr", "Open PR", show=False),
        Binding("full_stop", "open_editor", "Open in editor", show=False),
        Binding("D", "open_desktop", "Open in Claude desktop", show=False),
        Binding("A", "auto_archive", "Auto-archive", show=False),
        Binding("right_square_bracket", "jump(1)", "Next project", show=False),
        Binding("left_square_bracket", "jump(-1)", "Prev project", show=False),
        Binding("escape", "clear", "Clear", show=False),
        Binding("ctrl+r", "reload", "Reload", show=False),
        Binding("ctrl+backslash", "toggle_focus", "List / preview", show=False),
        Binding("t", "transcript", "Transcript"),
        Binding("dollar_sign", "costs", "Costs"),
        Binding("W", "worktrees", "Worktrees", show=False),
        Binding("S", "summary", "Summary", show=False),
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
        self.shown_ids: list[str] = []  # every session visible beside the list
        self.git_state: dict[str, tuple[int, int, int]] = {}  # project: (uncommitted, ahead, behind)
        self.paths = paths or data.Paths()
        self.digests = summary.Digests(self.paths.summaries)
        self.said: dict[tuple[str, float], str | None] = {}  # (id, mtime) -> end of last assistant message
        self.pulse_on = True
        self.same_name: set[str] = set()  # titles shared by more than one listed session
        self.row_args: dict[str, bool] = {}  # session id -> with_project, to redraw a row in place
        self.focus_id = focus_id
        self.state = data.State(self.paths.state)
        self.sessions: list[Session] = []
        self.live: dict[str, LiveSession] = {}
        self.notifications = notifications
        # finished a turn since you last opened them: session id -> since when; memory only
        self.waiting: dict[str, float] = {}
        self.permission: dict[str, str] = {}  # from hooks: session id -> what it's asking for
        self.permission_at: dict[str, float] = {}
        self.wait_labels: dict[str, str] = {}  # what the rows show, to redraw when a minute ticks over
        self.hook_waiting: set[str] = set()
        self.hook_notified: dict[str, float] = {}
        self.hook_seen: dict[str, float] = {}  # hook file time already dealt with (opened)
        self.last_status: dict[str, str] | None = None
        self.filters: set[str] = {"archived"} if show_archived else set()
        self.query_text = ""
        self.mode = "filter"  # what the search box is doing: "filter" or "search"
        self.search_query = ""
        self.hits: dict[str, list[str]] | None = None
        self.expanded: set[str] = set()
        self.trail: list[str] = []  # sessions tab jumped to, for shift+tab
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
        yield Activity(id="activity")
        yield Static("Loading sessions…", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.pulse_timer = self.set_interval(PULSE, self.pulse, pause=True)
        self.theme = self.theme_name
        # Rich styles `inline code` "on black"; keep the terminal's background instead. "warn" is
        # the terminal's yellow on dark themes, but light themes often make yellow unreadably pale.
        warn = "yellow" if self.current_theme.dark else WARN_ON_LIGHT
        self.console.push_theme(RichTheme({"markdown.code": "bold cyan", "warn": warn,
                                           "warn.bold": f"bold {warn}", "warn.italic": f"italic {warn}",
                                           "warn.dim": f"dim {warn}"}))
        self.query_one("#search", Input).border_title = "filter"
        self.query_one(SessionList).focus()
        if self.host:
            self.poll_host()
        self.load()
        self.set_interval(2, self.poll_live)
        self.set_interval(20, self.load)
        self.set_interval(60, self.refresh_prs)
        self.set_interval(120, self.refresh_summaries)

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
            self.refresh_summaries()
        self.refresh_git()

    @work(thread=True, exclusive=True, group="git")
    def refresh_git(self) -> None:
        """Uncommitted and unpushed counts for each project's checkout, shown on its header."""
        projects = {s.project for s in self.sessions if s.project}
        state = {p: st for p in projects if os.path.isdir(p) and (st := worktrees.repo_state(p))}
        self.call_from_thread(self.set_git_state, state)

    def set_git_state(self, state: dict[str, tuple[int, int, int]]) -> None:
        if state != self.git_state:
            self.git_state = state
            self.rebuild()

    def poll_live(self) -> None:
        live = data.load_live(self.paths)
        changed = self.host is not None and self.poll_host()
        changed = self.state.reload() or changed  # archived, pinned, tags... edited elsewhere
        before = set(self.waiting), dict(self.permission)
        self.track_hooks(live)
        self.track(live)
        changed = self.run_when_idle(live) or changed
        if (changed or (set(self.waiting), self.permission) != before or self.labels() != self.wait_labels
                or {k: v.status for k, v in live.items()} != {k: v.status for k, v in self.live.items()}
                or data.viewers(live) != self.viewers):
            self.live = live
            self.rebuild()

    def track(self, live: dict[str, LiveSession]) -> None:
        """Mark sessions that went from working to idle as waiting. The first call has no history."""
        prev, self.last_status = self.last_status, {k: v.status for k, v in live.items()}
        keep = {k for k, v in live.items() if v.status == "idle"} | self.hook_waiting
        self.waiting = {k: t for k, t in self.waiting.items() if k in keep}
        for sid, now in self.last_status.items():
            if prev is None or now != "idle" or prev.get(sid, "idle") == "idle" or sid in self.waiting:
                continue
            self.waiting[sid] = time.time()
            if self.notifications and sid != self.shown_id:
                title = next((x.title for x in self.sessions if x.id == sid), None) or live[sid].name or sid[:8]
                self.send_notification(f"{title} is waiting")

    def track_hooks(self, live: dict[str, LiveSession]) -> None:
        """Apply states written by `csm hook`. Opening a session ignores its file until a newer one."""
        self.permission, self.permission_at, self.hook_waiting = {}, {}, set()
        for sid, h in hooks.load_status(self.paths.status).items():
            if sid not in live or h["at"] <= self.hook_seen.get(sid, 0) or h["state"] == "working":
                continue
            perm = h["state"] == "permission"
            if perm:
                self.permission[sid] = h.get("message") or "permission"
                self.permission_at[sid] = h["at"]
            else:
                self.hook_waiting.add(sid)
                self.waiting[sid] = h["at"]  # when it stopped, even if csm wasn't running then
            if self.notifications and sid != self.shown_id and self.hook_notified.get(sid, 0) < h["at"]:
                title = next((x.title for x in self.sessions if x.id == sid), None) or live[sid].name or sid[:8]
                self.send_notification(f"{title} needs permission" if perm else f"{title} is waiting")
            self.hook_notified[sid] = h["at"]

    def attention(self) -> dict[str, float]:
        """Sessions that need you -> since when."""
        return {**self.waiting, **self.permission_at}

    def labels(self) -> dict[str, str]:
        return {sid: waited(t) for sid, t in self.attention().items()}

    @work(thread=True, group="notify")
    def send_notification(self, message: str) -> None:
        notify.send(message)  # may wait on tmux; keep it off the UI thread

    def poll_host(self) -> bool:
        """Refresh which sessions run on our tmux server. Returns True if anything changed."""
        try:
            hosted, shown = self.host.hosted(), self.host.shown_all()
        except (subprocess.CalledProcessError, OSError):
            return False
        changed = (hosted, shown) != (self.hosted, self.shown_ids)
        self.hosted, self.shown_ids = hosted, shown
        self.shown_id = shown[0] if shown else None
        return changed


    # ---- list ------------------------------------------------------------

    @property
    def narrowed(self) -> bool:
        return bool(self.query_text.strip() or self.hits is not None or self.filters - {"archived"})

    def visible(self) -> list[Session]:
        tokens = self.query_text.lower().split()
        out: list[Session] = []
        for s in self.sessions:  # newest first, so projects come out ordered by recent activity
            if self.archived_by(s) and "archived" not in self.filters and not self.active(s):
                continue
            if "pr" in self.filters and not s.pr_number:
                continue
            if "worktree" in self.filters and not s.worktree:
                continue
            if "live" in self.filters and s.id not in self.live:
                continue
            if "waiting" in self.filters and s.id not in self.waiting and s.id not in self.permission:
                continue
            if self.hits is not None and s.id not in self.hits:
                continue
            haystack = f"{s.project_name} {s.title} {s.branch or ''} {self.state.notes.get(s.id, '')}".lower()
            tags = self.state.tags.get(s.id, [])
            if any(not any(g.startswith(t[1:]) for g in tags) if t.startswith("#") else t not in haystack
                   for t in tokens):
                continue
            out.append(s)
        return out

    def active(self, s: Session) -> bool:
        """Working, waiting on you, or open beside the list: shown even when archived."""
        live = self.live.get(s.id)
        return bool(live and live.status != "idle") or s.id in self.waiting or s.id in self.permission \
            or self.displayed(s.id)

    @property
    def viewers(self) -> dict[str, str]:
        """{background session id: terminal session attached to it}."""
        return data.viewers(self.live)

    def displayed(self, sid: str) -> bool:
        """Beside the list, directly or through a terminal attached to its background job."""
        return sid in self.shown_ids or self.viewers.get(sid, "") in self.shown_ids

    def viewing(self, sid: str) -> str | None:
        """The background session this terminal session is attached to, if any."""
        return next((bg for bg, v in self.viewers.items() if v == sid), None)

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
        return "auto" if self.auto_reason(s) else None

    def auto_reason(self, s: Session) -> str | None:
        st = self.pr_status.get(s.pr_url or "")
        exempt = s.id in self.live or s.id in self.state.pinned or s.id in self.state.keep
        return autoarchive.reason(self.state.auto_archive, s.mtime, st.merged_at if st else None, time.time(), exempt)

    def pending_groups(self) -> dict[str, list[Pending]]:
        known = {s.id for s in self.sessions}
        self.pending = {k: p for k, p in self.pending.items() if k not in known and k in self.hosted}
        out: dict[str, list[Pending]] = {}
        for p in reversed(self.pending.values()):
            out.setdefault(p.project, []).append(p)
        return out if not self.narrowed else {}

    def pending_row(self, p: Pending) -> Text:
        dot = "» " if p.id in self.shown_ids else "● "
        return Text.assemble((dot, "cyan"), ("○ ", "dim"), (p.launch.label, "italic"))

    def columns(self, s: Session, with_project: bool) -> tuple[Text, Text]:
        """Leading mark column (only while something is marked) and trailing project name."""
        mark = Text("✓ " if s.id in self.marked else "  ", style="bold green") if self.marked else Text()
        return mark, Text(f"  {s.project_name}", style="dim") if with_project else Text()

    def row(self, s: Session, with_project: bool = False) -> Text:
        mark, project = self.columns(s, with_project)
        live = self.live.get(s.id)
        style = STATUS_STYLE.get(live.status, "cyan") if live else ""
        dot = self.marker(s, live, style)
        icon = ("⇄ ", pr_style(self.pr_status.get(s.pr_url or ""))) if s.pr_number else ("⑂ ", "magenta") if s.worktree else ("○ ", "dim")
        title = "dim italic" if self.archived_by(s) else "bold" if s.id in self.waiting or s.id in self.permission else ""
        fork = self.fork_tag(s)
        tags = Text("  " + " ".join(f"#{t}" for t in self.state.tags[s.id]), style="dim") if s.id in self.state.tags else Text()
        bg = Text("  bg", style="dim") if live and live.kind == "bg" else Text()
        then = Text(f"  @{q['do']}", style="cyan") if (q := self.state.when_idle.get(s.id)) else Text()
        wait = (f"{waited(t)} ", dot[1]) if (t := self.attention().get(s.id)) else ""
        return Text.assemble(mark, dot, icon, wait, (s.title, title), fork, bg, then, context_flag(s), tags, project)

    def fork_tag(self, s: Session) -> Text:
        """Forks copy their original's title; when both are listed, say which is which."""
        if s.title in self.same_name:
            label = "⑃ fork" if s.forked_from else "original" if self.is_parent(s) else "same name"
            return Text(f"  {label} · {ago(s.mtime)}", style="dim")
        return Text(" ⑃", style="dim") if s.forked_from else Text()

    def is_parent(self, s: Session) -> bool:
        return any(x.forked_from == s.id for x in self.sessions)

    def marker(self, s: Session, live: LiveSession | None, style: str) -> tuple[str, str]:
        if self.viewing(s.id):  # only a window onto a background job; that job's row carries the state
            return "⇢ ", "dim"
        if self.displayed(s.id):
            return "» ", style
        if s.id in self.permission:
            return "? ", "bold red"
        if s.id in self.waiting:
            return "◆ ", f"{style}.bold" if style == "warn" else f"bold {style}"
        if live and live.status != "idle" and not self.pulse_on:  # working: a slow pulse
            return "● ", "warn.dim" if style == "warn" else f"dim {style}"
        return ("● " if live else "  "), style

    def pulse(self) -> None:
        """Redraw only the working sessions' rows, so the pulse doesn't rebuild the list."""
        self.pulse_on = not self.pulse_on
        lst = self.query_one(SessionList)
        for sid, with_project in self.row_args.items():
            if (live := self.live.get(sid)) and live.status != "idle" and (s := self.by_id.get(sid)):
                lst.replace_option_prompt(f"s:{sid}", self.row(s, with_project))

    def rebuild(self) -> None:
        self.wait_labels = self.labels()
        lst = self.query_one(SessionList)
        current = lst.highlighted_option.id if lst.highlighted_option else None
        was = lst.highlighted  # where the cursor sat, for when its row disappears
        keep = f"s:{self.focus_id}" if self.focus_id else current
        self.focus_id = None
        visible = self.visible()
        titles = Counter(s.title for s in visible)
        self.same_name = {t for t, n in titles.items() if n > 1}
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
        if not self.narrowed:
            options.append(Option(self.summary_row(), id="S:summary"))  # groups add their own spacer
        self.by_id, self.row_args = {}, {}
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
                    ("▸ " if collapsed else "▾ ", "dim"), (name, "bold"), (f"  {len(sessions) + len(new)}", "dim"),
                    git_flags(self.git_state.get(project))), id=f"p:{project}"))
            for p in [] if collapsed else new:
                options.append(Option(self.pending_row(p), id=f"s:{p.id}"))
            for s in shown:
                self.by_id[s.id] = s
                self.row_args[s.id] = with_project
                options.append(Option(self.row(s, with_project), id=f"s:{s.id}"))
            if len(shown) < len(sessions) and not collapsed:
                options.append(Option(Text(f"    … {len(sessions) - len(shown)} more", style="dim italic"),
                                      id=f"m:{project}"))
        attached = set(self.viewers.values())
        lst.open_ids = {f"s:{i}" for i in self.shown_ids if i not in attached} | \
                       {f"s:{bg}" for bg, v in self.viewers.items() if v in self.shown_ids}
        lst.clear_options()
        lst.add_options(options)
        ids = [o.id for o in lst.options]
        if keep and keep in ids:
            lst.highlighted = ids.index(keep)
        elif ids:
            rows = [i for i, x in enumerate(ids) if x and x.startswith("s:")]
            if was is not None and current and current.startswith("s:") and rows:
                # The highlighted session went away (archived, trashed): stay put, on the next one.
                lst.highlighted = next((i for i in rows if i >= was), rows[-1])
            else:
                lst.highlighted = rows[0] if rows else 0
        else:
            self.show(None)
        self.update_status(len(visible))

    def update_status(self, count: int) -> None:
        busy = sum(1 for v in self.live.values() if v.status != "idle")
        self.query_one(Activity).set_busy(busy)
        if busy:
            self.pulse_timer.resume()
        else:
            self.pulse_timer.pause()
            self.pulse_on = True
        parts = [f"{count} sessions", f"{len(self.live)} live"]
        if self.permission:
            parts.append(f"{len(self.permission)} need permission")
        if self.waiting:
            parts.append(f"{len(self.waiting)} waiting (longest {waited(min(self.waiting.values()))})")
        if only := sorted(self.filters - {"archived"}):
            parts.append("only " + ", ".join(FILTER_NAMES[f] for f in only))
        if self.state.auto_archive["enabled"] and (n := sum(self.archived_by(s) == "auto" for s in self.sessions)):
            parts.append(f"{n} auto-archived")
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
        if event.option.id == "S:summary":
            self.show_summary()
        else:
            self.show(self.selected())

    @on(OptionList.OptionSelected)
    def chosen(self, event: OptionList.OptionSelected) -> None:
        kind, key = event.option.id[:2], event.option.id[2:]
        if kind == "S:":
            self.action_summary()
        elif kind == "p:":
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

    # ---- summary ---------------------------------------------------------

    @work(thread=True, exclusive=True, group="summaries")
    def refresh_summaries(self) -> None:
        worker = get_current_worker()
        hidden = {s.id for s in self.sessions if self.archived_by(s)}
        digests = summary.refresh(self.paths, [s for s in self.sessions if s.id not in hidden], dict(self.live),
                                  cancelled=lambda: worker.is_cancelled)
        if not worker.is_cancelled:
            self.call_from_thread(self.set_digests, digests)

    def set_digests(self, digests: summary.Digests) -> None:
        self.digests = digests
        self.rebuild()
        opt = self.query_one(SessionList).highlighted_option
        if opt and opt.id == "S:summary":
            self.show_summary()

    def summary_page(self) -> summary.Page:
        return summary.build(self.sessions, self.live, self.waiting, self.permission, self.digests,
                             self.last_said, hidden=lambda s: bool(self.archived_by(s)) and not self.active(s))

    def last_said(self, s: Session) -> str | None:
        """The end of the session's last assistant message: usually the question it's waiting on."""
        key = (s.id, s.mtime)
        if key not in self.said:
            try:
                text = next((m.text for m in reversed(data.transcript(s.path, limit=6)) if m.role == "assistant"), "")
            except OSError:
                text = ""
            last = [p for p in text.strip().split("\n\n") if p.strip()][-1:] or [""]
            line = " ".join(last[0].split())
            self.said[key] = (line[:157] + "…") if len(line) > 160 else line or None
        return self.said[key]

    def summary_row(self) -> Text:
        return Text.assemble(("◎ ", "bold cyan"), ("Summary", "bold"), (f"  {summary.counts(self.summary_page())}", "dim"))

    def show_summary(self) -> None:
        self.query_one("#meta", Static).update("")
        self.query_one("#messages", Static).update(summary.render(self.summary_page(), ago))
        self.query_one("#transcript", VerticalScroll).scroll_home(animate=False)

    def action_summary(self) -> None:
        def done(s: Session | None) -> None:
            if s:
                self.focus_id = s.id
                self.rebuild()
                self.resume(s)
        self.push_screen(summary.screen(self.summary_page(), ago), done)

    # ---- preview ---------------------------------------------------------

    def action_toggle_focus(self) -> None:
        """ctrl+\\ with no session beside the list: move between the list and the preview."""
        lst = self.query_one(SessionList)
        transcript = self.query_one("#transcript")
        if lst.has_focus and not self.screen.has_class("-narrow"):
            transcript.focus()
        else:
            lst.focus()

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
        if s.forked_from:
            parent = next((x for x in self.sessions if x.id == s.forked_from), None)
            lines.append(Text(f"⑃ forked from {parent.title if parent else s.forked_from}", style="dim"))
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
        if ctx := context_line(s):
            lines.append(ctx)
        if bg := self.viewing(s.id):
            job = self.by_id.get(bg)
            lines.append(Text(f"⇢ showing background job “{job.title if job else bg[:8]}”", style="dim"))
        elif live := self.live.get(s.id):
            where = "the background" if live.kind == "bg" else live.entrypoint or "claude"
            lines.append(Text.assemble(("● ", STATUS_STYLE.get(live.status, "cyan")),
                                       f"{live.status} in {where} (pid {live.pid})"))
            if live.kind == "bg":
                v = self.live.get(self.viewers.get(s.id, ""))
                lines.append(Text(f"  attached in tmux pane {v.tmux.rsplit('.', 1)[-1]}" if v and v.tmux
                                  else "  attached to a terminal" if v else "  no terminal attached", style="dim"))
        if s.id in self.permission:
            since = f" ({waited(self.permission_at[s.id])})" if s.id in self.permission_at else ""
            lines.append(Text(f"? needs permission{since}: {self.permission[s.id]}", style="bold red"))
        elif s.id in self.waiting:
            lines.append(Text(f"◆ waiting for you for {waited(self.waiting[s.id])}", style="bold"))
        if s.pr_url:
            st = self.pr_status.get(s.pr_url)
            lines.append(Text.assemble(("⇄ ", pr_style(st)), (f"#{s.pr_number} ", "bold"),
                                       pr_summary(st) + "  " if st else "", (s.pr_url, "dim")))
        if tags := self.state.tags.get(s.id):
            lines.append(Text(" ".join(f"#{t}" for t in tags), style="cyan"))
        if note := self.state.notes.get(s.id):
            lines.append(Text.assemble(("note: ", "dim"), note))
        if by := self.archived_by(s):
            lines.append(Text(self.auto_reason(s) if by == "auto" else f"archived in {by}", style="warn.italic"))
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

    def action_loadout(self) -> None:
        if (s := self.selected()) is not None:
            self.push_screen(LoadoutView(s))

    def action_transcript(self) -> None:
        if (s := self.selected()) is not None:
            self.push_screen(Viewer(s, self.current_theme.dark))

    def action_worktrees(self) -> None:
        self.push_screen(Worktrees(self.paths, self.sessions, self.live, Confirm))

    def action_help(self) -> None:
        self.push_screen(Help())

    # ---- actions on a session --------------------------------------------

    def resume(self, s: Session) -> None:
        if s.id in self.waiting or s.id in self.permission:
            self.clear_attention(s.id)
            self.rebuild()
        self.resume_flow(s)

    @work(thread=True, exclusive=True, group="resume")
    def resume_flow(self, s: Session) -> None:
        if s.id in self.hosted:
            self.call_from_thread(self.start, s)
            return
        if (v := self.viewers.get(s.id)) in self.hosted:  # a csm pane is showing this background job
            self.call_from_thread(self.start_launch, launch.resume(v, s.cwd, s.title))
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

    def start(self, s: Session, also: bool = False) -> None:
        self.start_launch(launch.resume(s.id, s.cwd, s.title), also)

    def clear_attention(self, sid: str) -> None:
        self.waiting.pop(sid, None)
        self.permission.pop(sid, None)
        self.permission_at.pop(sid, None)
        if sid in self.hook_notified:
            self.hook_seen[sid] = self.hook_notified[sid]
        self.hook_waiting.discard(sid)

    def start_launch(self, l: Launch, also: bool = False) -> None:
        self.clear_attention(l.focus_id)
        if not self.host:
            self.exit(l)
            return
        try:
            (self.host.show_also if also else self.host.show)(l.focus_id, l.cwd, l.command, l.label[:40])
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

    def action_new_project(self) -> None:
        """New session in any directory, including one Claude has never run in (or one to create)."""
        start = os.path.dirname(self.current_project() or "")
        if not os.path.isdir(start):
            start = next(d for d in (os.path.expanduser("~/projects"), os.path.expanduser("~")) if os.path.isdir(d))
        start = tilde(start).rstrip("/") + "/"

        def create(path: str, yes: bool) -> None:
            if not yes:
                return
            try:
                os.makedirs(path)
            except OSError as e:
                self.notify(f"Couldn't create {tilde(path)}: {e.strerror}", severity="error")
                return
            self.begin(launch.new(path), path)

        def done(text: str | None) -> None:
            if not text:
                return
            path = os.path.abspath(os.path.expanduser(text))
            if os.path.isdir(path):
                self.begin(launch.new(path), path)
            elif os.path.exists(path):
                self.notify(f"{tilde(path)} isn't a directory", severity="error")
            else:
                self.push_screen(Confirm(f"Create {tilde(path)}?"), lambda yes: create(path, yes))

        self.push_screen(DirPrompt("New session in", start), done)

    def action_fork(self) -> None:
        if s := self.selected():
            self.begin(launch.fork(s.id, s.cwd, s.title), s.project)

    def action_next_waiting(self, back: bool = False) -> None:
        """Open the session that has needed you longest (permission requests first); shift+tab goes back."""
        sessions = {s.id: s for s in self.sessions}
        if back:
            if self.trail:
                self.trail.pop()
            if not self.trail or not (s := sessions.get(self.trail[-1])):
                self.notify("No earlier one", timeout=2)
                return
        else:
            queue = sorted((t, sid) for sid, t in self.attention().items() if sid in sessions and sid not in self.permission)
            asking = sorted((t, sid) for sid, t in self.permission_at.items() if sid in sessions)
            if not (queue or asking):
                self.notify("Nothing waiting", timeout=2)
                return
            s = sessions[(asking or queue)[0][1]]
            self.trail = [*self.trail, s.id][-20:]
        self.expanded.add(s.project)  # it may sit past the first few rows of its project
        self.focus_id = s.id
        self.resume(s)
        self.rebuild()

    def action_open_also(self) -> None:
        if not self.host:
            self.notify("Side by side needs csm running in tmux", severity="warning")
        elif s := self.selected():
            self.start(s, also=True)

    def action_reply(self) -> None:
        if not (s := self.selected()):
            return
        if not self.host or s.id not in self.hosted:
            self.notify("Replies only work for sessions opened in csm", severity="warning")
            return

        def done(text: str | None) -> None:
            if not text:
                return
            try:
                sent = self.host.send(s.id, text)
            except subprocess.CalledProcessError as e:
                self.notify(f"tmux: {(e.stderr or '').strip() or e}", severity="error")
                return
            self.clear_attention(s.id)
            self.rebuild()
            self.notify(f"Sent to “{s.title}”" if sent else "That session is no longer running", timeout=2)
        self.push_screen(Prompt(f"Reply to {s.title}"), done)

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

    def retire_plan(self, targets: list[Session]) -> tuple[set[str], set[str], dict[str, int], list[str]]:
        """(ids to archive, csm panes to close by session id, background jobs to stop {id: pid},
        sessions left running elsewhere). A background job and the terminal attached to it go together."""
        ids = {s.id for s in targets}
        ids |= {self.viewers[i] for i in ids if i in self.viewers} | {bg for i in ids if (bg := self.viewing(i))}
        panes = {i for i in ids if self.host and i in self.hosted}
        jobs = {i: self.live[i].pid for i in ids if i in self.live and self.live[i].kind == "bg"}
        attached = set(self.viewers.values())
        elsewhere = [i for i in ids if i in self.live and i not in panes and i not in jobs and i not in attached]
        return ids, panes, jobs, elsewhere

    def action_retire(self) -> None:
        if not (targets := self.targets()):
            return
        ids, panes, jobs, elsewhere = self.retire_plan(targets)
        what = f"“{targets[0].title}”" if len(targets) == 1 else f"{len(targets)} sessions"
        steps = []
        if jobs:
            steps.append("stop background job" + "s" * (len(jobs) > 1) + " (pid " + ", ".join(map(str, sorted(jobs.values()))) + ")")
        if panes:
            steps.append(f"close {len(panes)} csm pane" + "s" * (len(panes) > 1))
        steps.append("archive " + ("it" if len(ids) == 1 else f"{len(ids)} sessions"))
        busy = [i for i in ids if (l := self.live.get(i)) and l.status != "idle"]
        msg = f"Retire {what}? This will {', '.join(steps)}."
        if busy:
            msg += f"\n{len(busy)} still working; that work stops too."
        if elsewhere:
            msg += f"\n{len(elsewhere)} running outside csm (Ghostty, desktop…) keep running; archived only."

        def done(yes: bool) -> None:
            if yes:
                self.retire(targets)
                self.marked.clear()
                self.notify(f"Retired {what}", timeout=2)
                self.rebuild()
        self.push_screen(Confirm(msg), done)

    def retire(self, targets: list[Session]) -> None:
        ids, panes, jobs, _ = self.retire_plan(targets)
        for i in panes:
            try:
                self.host.close(i)
            except subprocess.CalledProcessError:
                pass
        for pid in jobs.values():
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        self.state.archived |= ids
        self.state.save()
        if self.host:
            self.poll_host()

    def action_when_idle(self) -> None:
        if not (s := self.selected()):
            return
        queued = self.state.when_idle.get(s.id, {}).get("do")
        can_send = bool(self.host and s.id in self.hosted)

        def queue(entry: dict) -> None:
            self.state.when_idle[s.id] = entry
            self.state.save()
            self.notify(f"Will {entry['do']} “{s.title}” when it goes idle", timeout=2)
            self.rebuild()

        def picked(choice: str | None) -> None:
            if choice == "cancel":
                self.state.when_idle.pop(s.id, None)
                self.state.save()
                self.rebuild()
            elif choice == "send":
                self.push_screen(Prompt(f"Send to {s.title} when it goes idle"),
                                 lambda text: text and queue({"do": "send", "text": text}))
            elif choice:
                queue({"do": choice})
        self.push_screen(WhenIdle(s.title, queued, can_send), picked)

    def run_when_idle(self, live: dict[str, LiveSession]) -> bool:
        """Carry out queued actions for sessions that are idle or have stopped. True if any ran."""
        due = [sid for sid in self.state.when_idle if (l := live.get(sid)) is None or l.status == "idle"]
        for sid in due:
            entry = self.state.when_idle.pop(sid)
            s = self.by_id.get(sid)
            title = s.title if s else sid[:8]
            if entry["do"] == "notify":
                self.send_notification(f"{title} is idle")
            elif entry["do"] == "archive":
                self.state.archived.add(sid)
            elif entry["do"] == "retire" and s:
                self.retire([s])
            elif entry["do"] == "send" and self.host:
                try:
                    if self.host.send(sid, entry.get("text", "")):
                        self.clear_attention(sid)
                except subprocess.CalledProcessError:
                    pass
        if due:
            self.state.save()
        return bool(due)

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

    def action_tag(self) -> None:
        if not (targets := self.targets()):
            return
        many = len(targets) > 1
        current = "" if many else " ".join(f"#{t}" for t in self.state.tags.get(targets[0].id, []))
        title = f"Add tags to {len(targets)} sessions (#tag #other)" if many else "Tags (#tag #other)"

        def done(text: str | None) -> None:
            if text is None:
                return
            new = normalize_tags(text)
            for s in targets:
                self.state.set_tags(s.id, normalize_tags(self.state.tags.get(s.id, []) + new) if many else new)
            self.state.save()
            self.refresh_tagged(targets)
        self.push_screen(Prompt(title, current, allow_empty=not many), done)

    def action_note(self) -> None:
        if not (s := self.selected()):
            return

        def done(text: str | None) -> None:
            if text is None:
                return
            self.state.set_note(s.id, text)
            self.state.save()
            self.refresh_tagged([s])
        self.push_screen(Prompt("Note (empty removes it)", self.state.notes.get(s.id, ""), allow_empty=True), done)

    def refresh_tagged(self, targets: list[Session]) -> None:
        self.focus_id = targets[0].id
        self.rebuild()
        if (cur := self.selected()) and cur.id in {s.id for s in targets}:
            self.show(cur)

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
        if auto := [s for s in targets if self.archived_by(s) == "auto"]:
            self.state.keep |= {s.id for s in auto}
            self.state.save()
            self.notify(f"Keeping {len(auto)} auto-archived session{'s' * (len(auto) > 1)}", timeout=2)
            self.marked.clear()
            self.rebuild()
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

    def launch_external(self, fn, *args) -> bool:
        try:
            fn(*args)
            return True
        except OSError as e:
            self.notify(f"Couldn't open: {e}", severity="error")
            return False

    def action_open_pr(self) -> None:
        if not (s := self.selected()):
            return
        if not s.pr_url:
            self.notify("This session has no PR", severity="warning")
        elif self.launch_external(links.open_url, s.pr_url):
            self.notify("Opened PR in browser", timeout=2)

    def action_open_editor(self) -> None:
        s = self.selected()
        path = s.project if s else self.current_project()
        if not path or not os.path.isdir(path):
            self.notify("No project directory to open", severity="warning")
        else:
            self.launch_external(links.open_editor, path)

    def action_open_desktop(self) -> None:
        if not (s := self.selected()):
            return
        rec = self.desktop.get(s.id)
        if not rec or not desktop.SESSION_ID.match(rec.local_id):
            self.notify("Claude desktop doesn't know this session", severity="warning")
        elif self.launch_external(desktop.open_id, rec.local_id):
            self.notify("Opened in Claude desktop", timeout=2)

    def action_auto_archive(self) -> None:
        def done(rule: dict | None) -> None:
            if rule is not None:
                self.state.auto_archive = rule
                self.state.save()
                self.rebuild()
        self.push_screen(AutoArchiveSettings(self.state.auto_archive), done)

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
    if (code := hooks.dispatch(sys.argv[1:])) is not None:
        sys.exit(code)
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
