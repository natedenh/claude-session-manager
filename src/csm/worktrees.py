"""Worktree cleanup: find Claude Code worktrees that are done with, and remove them safely.

Only `git worktree remove` (never --force) and `git worktree prune` are used; branches are kept.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from . import prs
from .data import WORKTREE_MARK, LiveSession, Paths, Session

STALE_DAYS = 14
GIT_TIMEOUT = 20


@dataclass
class Worktree:
    repo: str
    path: str
    branch: str | None
    missing: bool
    sessions: list[Session] = field(default_factory=list)
    live: bool = False
    last_active: float = 0.0
    pr: str | None = None  # state of the newest linked PR we have cached
    dirty: int | None = None  # changed files; None if git couldn't say
    unpushed: int | None = None  # commits not on any remote branch
    candidate: bool = False
    reason: str = ""

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def project(self) -> str:
        return os.path.basename(self.repo) or self.repo


def git(args: list[str], cwd: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None


def repo_state(path: str) -> tuple[int, int, int] | None:
    """(uncommitted files, commits ahead of upstream, behind) for a checkout; None if it isn't one."""
    r = git(["status", "--porcelain=v1", "--branch"], path)
    if not r or r.returncode != 0:
        return None
    head, *files = r.stdout.splitlines() or [""]
    ahead = re.search(r"ahead (\d+)", head)
    behind = re.search(r"behind (\d+)", head)
    return len(files), int(ahead.group(1)) if ahead else 0, int(behind.group(1)) if behind else 0


def real(path: str) -> str:
    return os.path.realpath(path)


def registered(repo: str) -> list[tuple[str, str | None]]:
    """(path, branch) for each worktree git has registered under <repo>/.claude/worktrees."""
    r = git(["worktree", "list", "--porcelain"], repo)
    if not r or r.returncode:
        return []
    prefix = real(repo) + WORKTREE_MARK
    out, path, branch = [], None, None
    for line in (r.stdout + "\n").splitlines():
        if line.startswith("worktree "):
            path, branch = line[9:], None
        elif line.startswith("branch "):
            branch = line[7:].removeprefix("refs/heads/")
        elif not line and path:
            if real(path).startswith(prefix):
                out.append((path, branch))
            path = None
    return out


def count(args: list[str], cwd: str) -> int | None:
    r = git(args, cwd)
    return len(r.stdout.splitlines()) if r and not r.returncode else None


def inspect(wt: Worktree) -> None:
    if wt.missing:
        return
    wt.dirty = count(["status", "--porcelain"], wt.path)
    wt.unpushed = count(["log", "--oneline", wt.branch or "HEAD", "--not", "--remotes"], wt.path)


def judge(wt: Worktree, now: float) -> None:
    if wt.live:
        return
    if wt.missing:
        wt.candidate, wt.reason = True, "missing"
    elif wt.pr in ("merged", "closed"):
        wt.candidate, wt.reason = True, f"PR {wt.pr}"
    elif wt.last_active and now - wt.last_active >= STALE_DAYS * 86400:
        wt.candidate, wt.reason = True, f"idle {int((now - wt.last_active) // 86400)}d"


def discover(paths: Paths, sessions: Iterable[Session], live: dict[str, LiveSession],
             now: float | None = None, deep: bool = True) -> list[Worktree]:
    """Every registered worktree of every known project, candidates first."""
    now = time.time() if now is None else now
    sessions = list(sessions)
    repos = sorted({s.project for s in sessions if os.path.isdir(s.project)})
    found: list[Worktree] = []
    for repo in repos:
        for path, branch in registered(repo):
            found.append(Worktree(repo, path, branch, missing=not os.path.isdir(path)))
    live_cwds = [real(v.cwd) for v in live.values() if v.cwd]
    for wt in found:
        key = real(wt.path)
        wt.sessions = sorted((s for s in sessions if any(real(w) == key for w in s.worktrees)),
                             key=lambda s: -s.mtime)
        wt.live = any(s.id in live for s in wt.sessions) or any(c == key or c.startswith(key + os.sep) for c in live_cwds)
        if wt.sessions:
            wt.last_active = wt.sessions[0].mtime
        elif not wt.missing:
            wt.last_active = os.stat(wt.path).st_mtime
        for s in wt.sessions:
            if s.pr_url and (st := prs.cached(paths, s.pr_url)):
                wt.pr = st.state
                break
        judge(wt, now)
    if deep:
        for wt in found:
            inspect(wt)
    found.sort(key=lambda w: (not w.candidate, w.last_active))
    return found


def remove(wt: Worktree) -> tuple[bool, str]:
    """Remove a clean worktree, or prune a missing one. Returns (ok, git's message)."""
    args = ["worktree", "prune"] if wt.missing else ["worktree", "remove", wt.path]
    r = git(args, wt.repo)
    if r is None:
        return False, "git did not finish"
    msg = (r.stderr or r.stdout).strip()
    return r.returncode == 0, msg


def confirm_text(wt: Worktree) -> Text:
    """Styled Text rather than markup: names and paths can hold brackets."""
    where = Text.assemble((wt.project, "bold"), " / ", (wt.name, "bold"))
    if wt.missing:
        return Text.assemble("Prune ", where, "?\nIts directory is gone; this runs `git worktree prune` for ",
                             wt.project, ", dropping git's record of every missing worktree there.")
    lines = [Text.assemble("Remove worktree ", where, "?"), Text(wt.path, style="dim")]
    if wt.branch:
        lines.append(Text.assemble("Branch ", (wt.branch, "bold"), " is kept."))
    if wt.dirty is None or wt.unpushed is None:
        lines.append(Text("Couldn't read its git state; git will refuse if it has changes.", style="bold red"))
    if wt.dirty:
        lines.append(Text(f"WARNING: {wt.dirty} uncommitted change(s); git will refuse to remove it.", style="bold red"))
    if wt.unpushed:
        lines.append(Text(f"WARNING: {wt.unpushed} commit(s) not pushed to any remote. They stay on the branch.",
                          style="bold red"))
    return Text("\n").join(lines)

def ago_days(ts: float, now: float) -> str:
    return "-" if not ts else "today" if now - ts < 86400 else f"{int((now - ts) // 86400)}d ago"


def row(wt: Worktree, now: float) -> tuple:
    def n(v, zero=""):
        return "?" if v is None else str(v) if v else zero
    changes = "" if wt.missing else "clean" if wt.dirty == 0 else n(wt.dirty)
    style = "dim" if wt.live else ""
    flag = Text("live" if wt.live else f"remove: {wt.reason}" if wt.candidate else "",
                style="green" if wt.live else "warn")
    return (wt.project, wt.name, wt.branch or "-", str(len(wt.sessions)), ago_days(wt.last_active, now),
            wt.pr or "", "missing" if wt.missing else changes, "" if wt.missing else n(wt.unpushed), flag), style


class Worktrees(Screen[None]):
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close", show=False),
        Binding("W", "close", "Close", show=False),
        Binding("d", "remove", "Remove"),
        Binding("x", "remove", "Remove", show=False),
        Binding("j", "cursor('down')", show=False),
        Binding("k", "cursor('up')", show=False),
    ]
    DEFAULT_CSS = """
    Worktrees #head { padding: 1 2 0 2; }
    Worktrees DataTable { height: 1fr; margin: 1 2 0 2; background: transparent; }
    Worktrees #detail { height: auto; max-height: 10; padding: 1 2; }
    """
    COLUMNS = ("Project", "Worktree", "Branch", "Sessions", "Last active", "PR", "Changes", "Unpushed", "")

    def __init__(self, paths: Paths, sessions: list[Session], live: dict[str, LiveSession], confirm: Callable):
        super().__init__()
        self.paths, self.sessions, self.live, self.confirm = paths, sessions, live, confirm
        self.items: dict[str, Worktree] = {}
        self.busy = False

    def compose(self) -> ComposeResult:
        yield Static("Loading worktrees…", id="head")
        yield DataTable(cursor_type="row", zebra_stripes=False)
        yield Static("", id="detail")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns(*self.COLUMNS)
        table.focus()
        self.load()

    @work(thread=True, exclusive=True, group="scan")
    def load(self) -> None:
        found = discover(self.paths, self.sessions, self.live)
        self.app.call_from_thread(self.show, found)

    def show(self, found: list[Worktree]) -> None:
        table = self.query_one(DataTable)
        keep = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value if table.row_count else None
        table.clear()
        self.items = {w.path: w for w in found}
        now = time.time()
        for w in found:
            cells, style = row(w, now)
            table.add_row(*(Text(c, style=style) if isinstance(c, str) else c for c in cells), key=w.path)
        if keep in self.items:
            table.move_cursor(row=table.get_row_index(keep))
        cands = sum(w.candidate for w in found)
        self.query_one("#head", Static).update(
            f"{len(found)} worktrees, {cands} can go. Candidates: PR merged or closed, or idle "
            f"{STALE_DAYS}+ days, and not running." if found else "No worktrees under .claude/worktrees.")
        self.detail()

    def current(self) -> Worktree | None:
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        return self.items.get(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)

    @on(DataTable.RowHighlighted)
    def detail(self) -> None:
        wt = self.current()
        text = Text()
        if wt:
            text.append(wt.path + "\n", style="dim")
            for s in wt.sessions[:6]:
                text.append(f"  {s.title}\n")
            if len(wt.sessions) > 6:
                text.append(f"  … and {len(wt.sessions) - 6} more\n", style="dim")
            if not wt.sessions:
                text.append("  no sessions used this worktree\n", style="dim")
        self.query_one("#detail", Static).update(text)

    def action_cursor(self, direction: str) -> None:
        table = self.query_one(DataTable)
        table.action_cursor_down() if direction == "down" else table.action_cursor_up()

    def action_remove(self) -> None:
        wt = self.current()
        if not wt or self.busy:
            return
        if wt.live:
            self.notify("A session is running in that worktree.", severity="warning")
            return
        self.app.push_screen(self.confirm(confirm_text(wt)), lambda yes: self.run_remove(wt) if yes else None)

    def run_remove(self, wt: Worktree) -> None:
        self.busy = True
        self.remove_worker(wt)

    @work(thread=True, group="remove")
    def remove_worker(self, wt: Worktree) -> None:
        ok, msg = remove(wt)
        self.app.call_from_thread(self.removed, wt, ok, msg)

    def removed(self, wt: Worktree, ok: bool, msg: str) -> None:
        self.busy = False
        if ok:
            self.notify(f"{'Pruned' if wt.missing else 'Removed'} {wt.name}")
        else:
            self.notify(msg or "git refused", title="Not removed", severity="error", timeout=10, markup=False)
        self.load()

    def action_close(self) -> None:
        self.dismiss(None)
