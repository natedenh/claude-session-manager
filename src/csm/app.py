"""csm — browse, search and resume Claude Code sessions."""
from __future__ import annotations

import argparse
from collections import Counter
import os
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from rich.console import Group
from rich.markdown import Markdown
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.theme import Theme as RichTheme
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option
from textual.worker import get_current_worker

from . import autoarchive, data, desktop, export, hooks, links, models, prs, routines, recap, summary, tips, usage, tmux, worktrees
from .activity import STYLES as WAVES, Activity
from .attention import AttentionMixin
from .costs import Costs
from .hosting import HostingMixin
from .dialogs import AutoArchiveSettings, Confirm, Help, Prompt
from .fmt import (ago, context_flag, context_line, git_flags, last_days, pr_style, pr_summary,
                  sparkline, tilde, waited)
from .routines_view import Routines, overview as routines_overview
from .widgets import SessionList
from .stats import Stats
from .data import LiveSession, Message, Session, normalize_tags
from .launch import Pending
from .viewer import Viewer
from .loadout import LoadoutView
from .worktrees import Worktrees

PER_PROJECT = 5
PINNED = "__pinned__"  # pseudo-project key for the Pinned group
PULSE = 0.8  # seconds per half of a working session's dot pulse
WARN_ON_LIGHT = "#9a6700"  # dark amber: readable on light backgrounds
STATUS_STYLE = {"idle": "green", "busy": "warn"}
STUCK_MINUTES = 10  # busy with no transcript writes for this long looks stuck; CSM_STUCK_MINUTES, 0 = off
FILTER_NAMES = {"pr": "PRs", "worktree": "worktrees", "live": "live", "waiting": "waiting"}
MAX_MESSAGE_CHARS = 2500
THEMES = ("ansi-light", "ansi-dark")  # Textual's themes that use the terminal's own colors


def default_theme() -> str:
    """Match the terminal: its background shows through, so pick light or dark from macOS."""
    if theme := os.environ.get("CSM_THEME"):
        return theme
    try:  # macOS only; elsewhere, dark
        r = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True)
    except OSError:
        return "ansi-dark"
    return "ansi-dark" if r.stdout.strip() == "Dark" else "ansi-light"

class CSM(AttentionMixin, HostingMixin, App[Session | None]):
    TITLE = "Claude sessions"
    # Beside a session in tmux the list is all that fits; full width gets the preview back.
    HORIZONTAL_BREAKPOINTS = [(0, "-narrow"), (100, "-wide")]
    CSS = """
    #search { margin: 1 1; border-title-color: $accent; }  /* a blank line above, and before the list */
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
        Binding("B", "brief", "Continue fresh", show=False),
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
        Binding("ctrl+right_square_bracket", "next_waiting", "Next waiting", show=False),  # tmux sends it from any pane
        Binding("t", "transcript", "Transcript"),
        Binding("dollar_sign", "costs", "Costs"),
        Binding("I", "stats", "Stats", show=False),
        Binding("U", "routines", "Routines", show=False),
        Binding("J", "recap", "Recap", show=False),
        Binding("W", "worktrees", "Worktrees", show=False),
        Binding("S", "summary", "Summary", show=False),
        Binding("tilde", "next_wave", "Wave style", show=False),
        Binding("ctrl+t", "toggle_tips", "Tips on/off", show=False),
        Binding("question_mark", "help", "Help"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, paths: data.Paths | None = None, focus_id: str | None = None,
                 host: tmux.Tmux | None = None, show_archived: bool = False, theme: str | None = None,
                 notifications: bool = True, crashed: bool = False):
        super().__init__()
        self.crashed = crashed  # restarted after a crash: say so
        self.usage = usage.Usage((paths or data.Paths()).state.parent / "usage.json")
        self.signals = tips.Signals()
        self.moves = 0  # cursor moves since the last session opened
        self.last_action: tuple[str, float] = ("", 0.0)
        self.archived_at: list[float] = []
        self.via_tab = False
        self.theme_name = theme or default_theme()
        self.desktop: dict[str, data.DesktopRecord] = {}
        self.routines: list[routines.Routine] = []
        self.results_seen: set[tuple[str, str]] | None = None  # routine run results already announced
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
        self.stuck: dict[str, float] = {}  # busy but quiet: session id -> when its transcript last changed
        try:
            self.stuck_minutes = float(os.environ.get("CSM_STUCK_MINUTES") or STUCK_MINUTES)
        except ValueError:
            self.stuck_minutes = STUCK_MINUTES
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
        yield Activity(self.state.wave, id="activity")
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
        if self.crashed:
            self.notify(f"csm restarted after a crash; the details are in {tilde(str(self.crash_log))}",
                        severity="warning", timeout=15)
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
        rs = routines.load(self.paths)
        self.call_from_thread(self.set_sessions, sessions, live, desktop, rs)

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
                     desktop: dict[str, data.DesktopRecord], rs: list | None = None) -> None:
        if rs is not None:
            for r in rs:  # runs, newest first
                r.runs = sorted((rec for rec in desktop.values() if rec.routine == r.id), key=lambda x: -x.created)
            self.announce_results(rs)
        changed = ([(s.id, s.mtime, s.title) for s in sessions] != [(s.id, s.mtime, s.title) for s in self.sessions]
                   or desktop != self.desktop or (rs is not None and
                   [(r.id, r.enabled, r.last_run, len(r.runs)) for r in rs] != [(r.id, r.enabled, r.last_run, len(r.runs)) for r in self.routines]))
        if rs is not None:
            self.routines = rs
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

    def closing(self) -> bool:
        """True once the app is shutting down: its widgets go before timers, workers and queued
        events stop, and anything that touches them then would crash on the way out."""
        try:
            self.query_one(SessionList)
            return False
        except NoMatches:
            return True

    @property
    def crash_log(self) -> Path:
        return self.paths.state.parent / "crash.log"

    def notify(self, message: str, *, markup: bool = False, **kw) -> None:
        """Plain text unless asked: messages carry session titles and key names like `[`,
        which Textual would otherwise parse as markup, and a bad tag crashes the toast."""
        super().notify(message, markup=markup, **kw)

    async def run_action(self, action, default_namespace=None, namespaces=None) -> bool:
        if isinstance(action, str):
            self.note_action(action)
        return await super().run_action(action, default_namespace, namespaces)

    def on_unmount(self) -> None:
        self.usage.save(force=True)

    def _handle_exception(self, error: Exception) -> None:
        """Every unhandled error, in the app or a worker, ends here: keep its traceback."""
        try:
            self.crash_log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.crash_log, "a") as f:
                f.write(f"\n--- {datetime.now():%Y-%m-%d %H:%M:%S} pid {os.getpid()}\n")
                traceback.print_exception(getattr(error, "error", None) or error, file=f)  # a worker's own error
        except OSError:
            pass
        super()._handle_exception(error)

    def poll_live(self) -> None:
        if self.closing():
            return
        live = data.load_live(self.paths)
        changed = self.host is not None and self.poll_host()
        changed = self.state.reload() or changed  # archived, pinned, tags... edited elsewhere
        if (bar := self.query_one(Activity)).kind != (self.state.wave or WAVES[0]):
            bar.set_style(self.state.wave)
        before = set(self.waiting), dict(self.permission), set(self.stuck)
        self.track_hooks(live)
        self.track(live)
        self.track_stuck(live)
        changed = self.run_when_idle(live) or changed
        self.offer_tip()
        self.usage.save()
        if (changed or (set(self.waiting), self.permission, set(self.stuck)) != before or self.labels() != self.wait_labels
                or {k: v.status for k, v in live.items()} != {k: v.status for k, v in self.live.items()}
                or data.viewers(live) != self.viewers):
            self.live = live
            self.rebuild()

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
            if "waiting" in self.filters and not ({s.id} & (self.waiting.keys() | self.permission.keys() | self.stuck.keys())):
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
        icon = ("⇄ ", pr_style(self.pr_status.get(s.pr_url or ""))) if s.pr_number else ("⑂ ", "magenta") if s.worktree \
            else ("◷ ", "cyan") if (rec := self.desktop.get(s.id)) and rec.routine else ("○ ", "dim")
        title = "dim italic" if self.archived_by(s) else "bold" if s.id in self.waiting or s.id in self.permission else ""
        fork = self.fork_tag(s)
        tags = Text("  " + " ".join(f"#{t}" for t in self.state.tags[s.id]), style="dim") if s.id in self.state.tags else Text()
        bg = Text("  bg", style="dim") if live and live.kind == "bg" else Text()
        then = Text(f"  @{q['do']}", style="cyan") if (q := self.state.when_idle.get(s.id)) else Text()
        fam = models.family(s.context_model) if getattr(self, "mixed_models", False) else None
        then = Text.assemble((f"  {fam}", "dim"), then) if fam else then
        wait = (f"{waited(t)} ", dot[1]) if (t := self.attention().get(s.id) or self.stuck.get(s.id)) else ""
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
        if s.id in self.stuck:
            return "⧗ ", "warn"
        if live and live.status != "idle" and not self.pulse_on:  # working: a slow pulse
            return "● ", "warn.dim" if style == "warn" else f"dim {style}"
        return ("● " if live else "  "), style

    def pulse(self) -> None:
        """Redraw only the working sessions' rows, so the pulse doesn't rebuild the list."""
        if self.closing():
            return
        self.pulse_on = not self.pulse_on
        lst = self.query_one(SessionList)
        for sid, with_project in self.row_args.items():
            if (live := self.live.get(sid)) and live.status != "idle" and (s := self.by_id.get(sid)):
                lst.replace_option_prompt(f"s:{sid}", self.row(s, with_project))

    def rebuild(self) -> None:
        if self.closing():
            return
        self.wait_labels = self.labels()
        lst = self.query_one(SessionList)
        current = lst.highlighted_option.id if lst.highlighted_option else None
        was = lst.highlighted  # where the cursor sat, for when its row disappears
        keep = f"s:{self.focus_id}" if self.focus_id else current
        self.focus_id = None
        visible = self.visible()
        titles = Counter(s.title for s in visible)
        # Rows name their model only when the list mixes families; all-Opus rows would just be noise.
        self.mixed_models = len({models.family(s.context_model) for s in visible if s.context_model} - {None}) > 1
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
        days = last_days()
        recent: dict[str, list[int]] = {}  # project -> active minutes on each of the last days
        for x in self.sessions:
            if x.active:
                row = recent.setdefault(x.project, [0] * len(days))
                for i, d in enumerate(days):
                    row[i] += x.active.get(d, 0)
        top = max((m for row in recent.values() for m in row), default=0)  # one scale, so projects compare
        options: list[Option | None] = []
        if not self.narrowed:
            options.append(Option(self.summary_row(), id="S:summary"))  # groups add their own spacer
            if self.routines:
                options.append(Option(self.routines_row(), id="R:routines"))
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
                    git_flags(self.git_state.get(project)),
                    sparkline(recent.get(project, []), top) if project != PINNED else ""), id=f"p:{project}"))
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
        if self.stuck:
            parts.append(f"{len(self.stuck)} stuck")
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
        # Text, not markup: the parts carry what you typed into the filter.
        self.query_one("#status", Static).update(Text.assemble("  ·  ".join(parts), ("    ? help", "dim")))

    def selected(self) -> Session | None:
        opt = self.query_one(SessionList).highlighted_option
        return self.by_id.get(opt.id[2:]) if opt and opt.id and opt.id.startswith("s:") else None

    @on(OptionList.OptionHighlighted)
    def highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if self.closing():
            return
        if event.option.id == "S:summary":
            self.show_summary()
        elif event.option.id == "R:routines":
            self.show_routines()
        else:
            self.show(self.selected())

    @on(OptionList.OptionSelected)
    def chosen(self, event: OptionList.OptionSelected) -> None:
        kind, key = event.option.id[:2], event.option.id[2:]
        if kind == "S:":
            self.action_summary()
        elif kind == "R:":
            self.action_routines()
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

    def announce_results(self, rs: list) -> None:
        """A routine run's result, once, when it appears. The first load only takes note."""
        results = {(rec.cli_id or rec.local_id, rec.result): r.name for r in rs for rec in r.runs if rec.result}
        if self.results_seen is not None:
            for (_, text), name in results.items():
                if (_, text) not in self.results_seen:
                    self.notify(f"◷ {name}: {text}", timeout=15)
                    if self.notifications:
                        self.send_notification(f"{name}: {text}")
        self.results_seen = set(results)

    def routines_row(self) -> Text:
        active = [r for r in self.routines if r.status in ("active", "scheduled")]
        nxt = min((t for r in active if (t := r.next_run())), default=None)
        info = f"{len(active)} active" if active else "none active"
        if nxt:
            info += f" · next {routines.until(nxt)}"
        return Text.assemble(("◷ ", "bold cyan"), ("Routines", "bold"), (f"  {info}", "dim"))

    def show_routines(self) -> None:
        self.query_one("#meta", Static).update("")
        self.query_one("#messages", Static).update(routines_overview(self.routines))
        self.query_one("#transcript", VerticalScroll).scroll_home(animate=False)

    def action_routines(self) -> None:
        def done(sid: str | None) -> None:
            if sid and (s := next((x for x in self.sessions if x.id == sid), None)):
                self.focus_id = s.id
                self.expanded.add(s.project)
                self.filters.discard("waiting")
                self.rebuild()
        self.push_screen(Routines(self.routines, {s.id for s in self.sessions}), done)

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
        if (rec := self.desktop.get(s.id)) and rec.routine:
            name = next((r.name for r in self.routines if r.id == rec.routine), rec.routine)
            lines.append(Text.assemble(("◷ ", "cyan"), (f"a run of the routine “{name}”", "dim"),
                                       (f": {rec.result}" if rec.result else "", "")))
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
        elif s.id in self.stuck:
            lines.append(Text(f"⧗ looks stuck: busy, but nothing written for {waited(self.stuck[s.id])}"
                              " (or a long command is running)", style="warn"))
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
        if self.closing():
            return
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
                self.query_one("#status", Static).update(Text(f"searching transcripts for “{q}”…"))
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

    def action_stats(self) -> None:
        self.push_screen(Stats(self.sessions, self.usage))

    def action_recap(self) -> None:
        self.notify("Writing today's recap (summarizing what changed first)…", timeout=3)
        self.run_recap()

    @work(thread=True, exclusive=True, group="recap")
    def run_recap(self) -> None:
        day = datetime.now().date()
        hidden = {s.id for s in self.sessions if self.archived_by(s)}
        shown = [s for s in self.sessions if s.id not in hidden]
        digests = summary.refresh(self.paths, [s for s in shown if s.active.get(day.isoformat())], dict(self.live))
        pr = lambda s: pr_summary(st) if (st := self.pr_status.get(s.pr_url or "")) else ""
        text = recap.render(shown, day, digests, self.last_said, pr, set(self.waiting) | set(self.permission))
        where = Path(os.environ.get("CSM_RECAP_DIR") or self.paths.export)
        try:
            path = recap.write(text, day, where.expanduser())
        except OSError as e:
            self.call_from_thread(self.notify, f"Recap failed: {e}", severity="error")
            return
        note = f"Recap written to {tilde(str(path))}"
        if digests.error:
            note += " (summaries unavailable, so it uses each session's last message)"
        self.call_from_thread(self.notify, note, timeout=5)
        try:
            subprocess.run([links.OPENER, str(path)], check=False)
        except OSError:
            pass  # written; it just can't be opened here

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

    def action_next_wave(self) -> None:
        bar = self.query_one(Activity)
        kind = WAVES[(WAVES.index(bar.kind) + 1) % len(WAVES)]
        bar.set_style(kind)
        self.state.wave = kind
        self.state.save()
        self.notify(f"Activity style: {kind}  ({WAVES.index(kind) + 1} of {len(WAVES)}; ~ or click for the next)", timeout=2)

    async def action_quit(self) -> None:
        if self.host and self.host.own:
            self.host.detach()  # sessions keep running; `csm` reattaches
        else:
            self.exit()

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
    ap.add_argument("--stuck-minutes", help=f"flag a busy session with no output for this long; default {STUCK_MINUTES}, "
                                            "0 turns it off. Also CSM_STUCK_MINUTES.")
    ap.add_argument("--export-dir", help="where E writes Markdown exports; default ~/Downloads/claude-sessions. "
                                         "Also CSM_EXPORT_DIR.")
    ap.add_argument("--theme", help="Textual theme; default ansi-light or ansi-dark (follows macOS), "
                                    "which use the terminal's own colors and background. Also CSM_THEME.")
    ap.add_argument("--sidebar", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.stuck_minutes:
        os.environ["CSM_STUCK_MINUTES"] = args.stuck_minutes
    if args.export_dir:
        os.environ["CSM_EXPORT_DIR"] = os.path.abspath(os.path.expanduser(args.export_dir))
    if args.sidebar or (os.environ.get("TMUX") and not args.no_tmux):
        host = tmux.Tmux(own=args.sidebar)
        host.mark_sidebar()
        # If the sidebar crashes, start it again: its pane closing would leave the sessions beside
        # it without a list. A crash loop gives up after a few tries.
        crashes: list[float] = []
        while True:
            app = CSM(host=host, show_archived=args.archived, theme=args.theme,
                      notifications=not args.no_notify, crashed=bool(crashes))
            app.run()
            if not app.return_code:
                return
            crashes = [t for t in crashes if time.time() - t < 120] + [time.time()]
            if len(crashes) > 5:
                print(f"csm keeps crashing; see {tilde(str(app.crash_log))}. Press enter to close.")
                input()
                return
            time.sleep(1)
    if not args.no_tmux and tmux.available():
        extra = (["--archived"] if args.archived else []) + (["--no-notify"] if args.no_notify else []) + (["--theme", args.theme] if args.theme else [])
        extra += ["--export-dir", os.environ["CSM_EXPORT_DIR"]] if args.export_dir else []
        extra += ["--stuck-minutes", args.stuck_minutes] if args.stuck_minutes else []
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
