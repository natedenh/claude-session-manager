"""Hosting sessions next to the sidebar in tmux.

csm runs in the left pane of a window on its own tmux server (`tmux -L csm`, with the
config in tmux.conf, so a personal tmux setup is untouched). Each resumed session lives in
a pane tagged with its session id. Showing a session joins its pane to the right of the
sidebar; the pane that was there is broken back out into a hidden window and keeps running.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SOCKET = "csm"
SESSION = "csm"
CONF = Path(__file__).with_name("tmux.conf")
DEFAULT_SESSION_WIDTH = "70%"


def available() -> bool:
    return shutil.which("tmux") is not None


def sidebar_command(extra: list[str]) -> str:
    return shlex.join([sys.executable, "-m", "csm", "--sidebar", *extra])


def launch(extra: list[str]) -> None:
    """Attach to the csm tmux session, creating it (or a missing sidebar) first. Never returns."""
    base = ["tmux", "-L", SOCKET, "-f", str(CONF)]
    cmd = sidebar_command(extra)
    if subprocess.run([*base, "has-session", "-t", SESSION], capture_output=True).returncode == 0:
        t = Tmux(base)
        t.run("source-file", str(CONF))  # -f only applies when the server starts
        sidebar = next((p for p in t.panes() if p.sidebar), None)
        if sidebar:
            # Restart the sidebar so an updated csm takes effect; sessions are separate panes
            # and keep running.
            t.run("respawn-pane", "-k", "-t", sidebar.id, cmd)
            t.run("select-window", "-t", sidebar.window)
            t.run("select-pane", "-t", sidebar.id)
        else:  # csm was quit but sessions are still running; give them a sidebar again
            t.run("new-window", "-t", f"{SESSION}:", cmd)
        os.execvp(base[0], [*base, "attach-session", "-t", SESSION])
    os.execvp(base[0], [*base, "new-session", "-s", SESSION, cmd])


@dataclass
class Pane:
    id: str
    window: str
    session_id: str  # empty if the pane isn't hosting a Claude session
    sidebar: bool
    width: int
    window_width: int


class Tmux:
    """Talks to one tmux server. `me` is the sidebar's own pane."""

    def __init__(self, base: list[str] | None = None, me: str | None = None, own: bool = False):
        self.base = base or ["tmux"]  # inside tmux, plain `tmux` reaches the current server
        self.me = me or os.environ.get("TMUX_PANE", "")
        self.own = own  # our private server (quitting detaches) vs. the user's own tmux

    def run(self, *args: str) -> str:
        return subprocess.run([*self.base, *args], capture_output=True, text=True, check=True).stdout.strip()

    def panes(self) -> list[Pane]:
        out = self.run("list-panes", "-a", "-F",
                       "#{pane_id}\t#{window_id}\t#{@csm_session}\t#{@csm_sidebar}\t#{pane_width}\t#{window_width}")
        panes = []
        for line in out.splitlines():
            pid, win, sid, sidebar, w, ww = line.split("\t")
            panes.append(Pane(pid, win, sid, sidebar == "1", int(w), int(ww)))
        return panes

    def mark_sidebar(self) -> None:
        self.run("set-option", "-p", "-t", self.me, "@csm_sidebar", "1")

    def hosted(self) -> dict[str, str]:
        """{session id: pane id} for every session running on this server."""
        return {p.session_id: p.id for p in self.panes() if p.session_id}

    def shown_all(self) -> list[str]:
        """Session ids in the panes beside the sidebar, top to bottom."""
        panes = self.panes()
        mine = next((p for p in panes if p.id == self.me), None)
        if not mine:
            return []
        return [p.session_id for p in panes if p.window == mine.window and p.id != self.me and p.session_id]

    def shown(self) -> str | None:
        """Session id in the first pane beside the sidebar, if any."""
        return next(iter(self.shown_all()), None)

    def pane_for(self, panes: list[Pane], session_id: str, cwd: str, command: str, name: str) -> str:
        """The session's pane id, starting it in a hidden window if it isn't running."""
        target = next((p for p in panes if p.session_id == session_id), None)
        if target:
            return target.id
        pane = self.run("new-window", "-d", "-P", "-F", "#{pane_id}", "-c", cwd, "-n", name or session_id[:8], command)
        self.run("set-option", "-p", "-t", pane, "@csm_session", session_id)
        return pane

    def start(self, session_id: str, cwd: str, command: str, name: str = "") -> str:
        """Run the session in a hidden window, leaving the panes beside the sidebar alone."""
        return self.pane_for(self.panes(), session_id, cwd, command, name)

    def show(self, session_id: str, cwd: str, command: str, name: str = "") -> str:
        """Put only this session's pane beside the sidebar and focus it, starting it if needed."""
        panes = self.panes()
        mine = next(p for p in panes if p.id == self.me)
        target = next((p for p in panes if p.session_id == session_id), None)
        beside = [p for p in panes if p.window == mine.window and p.id != self.me]
        width = DEFAULT_SESSION_WIDTH
        if beside:
            width = str(max(20, mine.window_width - mine.width - 1))  # keep the user's split
        for p in beside:
            if not target or p.id != target.id:
                self.run("break-pane", "-d", "-s", p.id)
        if target and target.window == mine.window:
            self.run("select-pane", "-t", target.id)
            return target.id
        pane = self.pane_for(panes, session_id, cwd, command, name)
        self.run("join-pane", "-h", "-l", width, "-s", pane, "-t", self.me)
        self.run("select-pane", "-t", pane)
        return pane

    def show_also(self, session_id: str, cwd: str, command: str, name: str = "") -> str:
        """Show the session below the ones already beside the sidebar (plain show() if none)."""
        panes = self.panes()
        mine = next(p for p in panes if p.id == self.me)
        beside = [p for p in panes if p.window == mine.window and p.id != self.me]
        if not beside:
            return self.show(session_id, cwd, command, name)
        target = next((p for p in panes if p.session_id == session_id), None)
        if target and target.window == mine.window:
            self.run("select-pane", "-t", target.id)
            return target.id
        pane = self.pane_for(panes, session_id, cwd, command, name)
        self.run("join-pane", "-v", "-s", pane, "-t", beside[-1].id)
        self.run("select-pane", "-t", pane)
        return pane

    def send(self, session_id: str, text: str) -> bool:
        """Type text and Enter into the session's pane without showing it."""
        pane = self.hosted().get(session_id)
        if pane:
            self.run("send-keys", "-t", pane, "-l", "--", text)
            self.run("send-keys", "-t", pane, "Enter")
        return pane is not None

    def close(self, session_id: str) -> bool:
        pane = self.hosted().get(session_id)
        if pane:
            self.run("kill-pane", "-t", pane)
        return pane is not None

    def detach(self) -> None:
        self.run("detach-client")
