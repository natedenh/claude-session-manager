"""Which csm features you use, kept locally, to suggest the ones you aren't.

Only action names and dates are recorded, never anything about your sessions.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

KEEP_DAYS = 60
LEARNING_DAYS = 3  # before this much history, "never used" means little


@dataclass(frozen=True)
class Feature:
    action: str  # as bound, without an "app." prefix
    keys: str
    what: str


# Every feature worth suggesting; plain navigation and quitting aren't.
FEATURES = [
    Feature("open", "enter", "open a session beside the list (or where it's already running)"),
    Feature("next_waiting", "tab / ctrl+]", "open the session that has waited longest for you; ctrl+] works from inside a session"),
    Feature("toggle_focus", "ctrl+\\", "cycle focus between the list and the sessions beside it"),
    Feature("filter", "/", "filter by title, project, branch, note or #tag as you type"),
    Feature("search", "s", "search the text of every transcript"),
    Feature("jump(1)", "] / [", "jump to the next or previous project"),
    Feature("toggle('waiting')", "!", "show only sessions waiting for you (or stuck)"),
    Feature("toggle('live')", "l", "show only running sessions"),
    Feature("toggle('pr')", "p", "show only sessions with a PR"),
    Feature("toggle('worktree')", "w", "show only worktree sessions"),
    Feature("toggle('archived')", "a", "include archived sessions"),
    Feature("toggle_flat", "v", "one newest-first list instead of grouping by project"),
    Feature("expand_all", "e", "show every session, not just 5 per project"),
    Feature("new", "n", "new session in the highlighted project, optionally starting on a message"),
    Feature("new(True)", "N", "new session in a fresh git worktree"),
    Feature("new_project", "P", "new session in any directory, even one Claude has never run in"),
    Feature("brief", "B", "continue a long session fresh, from a brief of where it stands"),
    Feature("fork", "f", "fork a session to try something without disturbing it"),
    Feature("open_also", "|", "show a second session below the first"),
    Feature("reply", "R", "reply to a session without opening it"),
    Feature("when_idle", "@", "when a session goes idle: notify, archive, retire or send it a prompt"),
    Feature("mark", "space", "mark several sessions, then archive, export or retire them together"),
    Feature("archive", "x", "archive (or unarchive) a session"),
    Feature("retire", "X", "stop a session and archive it in one go"),
    Feature("pin", "*", "pin a session to the top"),
    Feature("tag", "#", "tag sessions, e.g. #waiting-on-chris; / finds them"),
    Feature("note", "i", "add a note to a session; / searches notes"),
    Feature("rename", "r", "rename a session"),
    Feature("transcript", "t", "read a whole transcript, with search"),
    Feature("loadout", "L", "see the plugins, skills, MCP servers and hooks a session loaded"),
    Feature("summary", "S", "summary of the last 48 hours: what needs you, decisions, finished work"),
    Feature("recap", "J", "write today's recap to Markdown"),
    Feature("stats", "I", "stats: Claude time, streak, busiest projects, cost by week"),
    Feature("costs", "$", "where the money went"),
    Feature("worktrees", "W", "clean up old worktrees"),
    Feature("export", "E", "export sessions to Markdown"),
    Feature("open_pr", "g", "open a session's PR in the browser"),
    Feature("open_editor", ".", "open the project in your editor"),
    Feature("open('tab')", "o / O", "resume in a new Ghostty tab or window"),
    Feature("auto_archive", "A", "archive merged and idle sessions automatically"),
    Feature("next_wave", "~", "change the activity strip's style"),
    Feature("help", "?", "every key, on one screen"),
]
BY_ACTION = {f.action: f for f in FEATURES}
ALIASES = {"jump(-1)": "jump(1)", "open('window')": "open('tab')", "next_waiting(True)": "next_waiting"}


def feature_of(action: str) -> str | None:
    name = action.removeprefix("app.")
    name = ALIASES.get(name, name)
    return name if name in BY_ACTION else None


class Usage:
    """{feature: {"days": {date: count}}} plus which tips were shown when, in usage.json."""

    def __init__(self, path: Path, today: date | None = None):
        self.path = path
        self.today = today
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            raw = {}
        self.since: str = raw.get("since") or self.day()
        self.counts: dict[str, dict[str, int]] = {k: dict(v) for k, v in (raw.get("days") or {}).items()
                                                   if isinstance(v, dict)}
        self.tips: dict[str, str] = dict(raw.get("tips") or {})  # tip id -> day last shown
        self.muted: bool = bool(raw.get("muted"))
        self.dirty, self.saved_at = False, 0.0

    def day(self) -> str:
        return (self.today or date.today()).isoformat()

    def record(self, action: str) -> str | None:
        if name := feature_of(action):
            days = self.counts.setdefault(name, {})
            days[self.day()] = days.get(self.day(), 0) + 1
            self.dirty = True
        return name

    def total(self, name: str) -> int:
        return sum(self.counts.get(name, {}).values())

    def last(self, name: str) -> str | None:
        return max(self.counts.get(name, {}), default=None)

    def since_days(self, name: str, days: int) -> int:
        start = (date.fromisoformat(self.day()) - timedelta(days=days - 1)).isoformat()
        return sum(n for d, n in self.counts.get(name, {}).items() if d >= start)

    @property
    def history_days(self) -> int:
        return (date.fromisoformat(self.day()) - date.fromisoformat(self.since)).days + 1

    def save(self, force: bool = False) -> None:
        """Every so often rather than on each key; force on exit."""
        if not self.dirty or (not force and time.time() - self.saved_at < 10):
            return
        cutoff = (date.fromisoformat(self.day()) - timedelta(days=KEEP_DAYS)).isoformat()
        days = {k: {d: n for d, n in v.items() if d >= cutoff} for k, v in self.counts.items()}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"since": self.since, "days": days, "tips": self.tips, "muted": self.muted}))
            tmp.replace(self.path)
            self.dirty, self.saved_at = False, time.time()
        except OSError:
            pass
