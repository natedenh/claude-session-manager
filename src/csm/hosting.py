"""Starting, showing and stopping sessions: tmux panes, new sessions, briefs, forks, replies,
retiring, and Ghostty. Mixed into the app (csm.app.CSM)."""
from __future__ import annotations

import os
import signal
import subprocess

from textual import work

from . import brief, desktop, ghostty, launch
from .data import Session
from .dialogs import Confirm, DirPrompt, FirstMessage, Prompt
from .fmt import context_fraction, tilde
from .launch import Launch, Pending
from .widgets import SessionList


class HostingMixin:
    """Where sessions run and how they start."""

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

    def resume(self, s: Session) -> None:
        if not self.via_tab:
            self.usage.record("open")
            if s.id in self.waiting or s.id in self.permission:
                self.signals.waiting_opened_by_hand += 1
        self.via_tab = False
        if (frac := context_fraction(s)) and frac > 0.8:
            self.signals.opened_full_context = True
        self.signals.long_scroll = self.signals.long_scroll or self.moves >= 20
        self.moves = 0
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

    def begin(self, l: Launch, project: str, hidden: bool = False) -> None:
        if not os.path.isdir(l.cwd):
            self.notify(f"{tilde(l.cwd)} no longer exists", severity="error")
            return
        if self.host:
            self.pending[l.focus_id] = Pending(l.focus_id, project, l)
            self.focus_id = l.focus_id
        if hidden and self.host:  # it has its instructions; stay in the list
            try:
                self.host.start(l.focus_id, l.cwd, l.command, l.label[:40])
            except subprocess.CalledProcessError as e:
                self.notify(f"tmux: {(e.stderr or '').strip() or e}", severity="error")
            self.poll_host()
            self.rebuild()
            self.notify(f"Started in {os.path.basename(project) or project}; enter to watch it", timeout=3)
            return
        self.start_launch(l)

    def new_in(self, project: str, worktree: bool = False) -> None:
        """Ask for a first message, then start a new session in the project."""
        def done(text: str | None) -> None:
            if text is not None:
                self.begin(launch.new(project, worktree, text), project, hidden=bool(text))
        self.push_screen(FirstMessage(tilde(project) + (" (new worktree)" if worktree else "")), done)

    def action_new(self, worktree: bool = False) -> None:
        if project := self.current_project():
            self.new_in(project, worktree)

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
            self.new_in(path)

        def done(text: str | None) -> None:
            if not text:
                return
            path = os.path.abspath(os.path.expanduser(text))
            if os.path.isdir(path):
                self.new_in(path)
            elif os.path.exists(path):
                self.notify(f"{tilde(path)} isn't a directory", severity="error")
            else:
                self.push_screen(Confirm(f"Create {tilde(path)}?"), lambda yes: create(path, yes))

        self.push_screen(DirPrompt("New session in", start), done)

    def action_brief(self) -> None:
        if s := self.selected():
            self.notify(f"Writing a brief of “{s.title}” for a new session…", timeout=4)
            self.run_brief(s)

    @work(thread=True, exclusive=True, group="brief")
    def run_brief(self, s: Session) -> None:
        try:
            text = brief.write(s)
        except Exception as e:  # credentials, network, refusal
            self.call_from_thread(self.notify, f"Couldn't write a brief: {type(e).__name__}: {e}"[:300], severity="error")
            return
        self.call_from_thread(self.continue_fresh, s, text)

    def continue_fresh(self, s: Session, text: str) -> None:
        """Offer the brief as the first message of a new session where the old one ran."""
        def done(message: str | None) -> None:
            if message is not None:
                self.begin(launch.new(s.cwd, prompt=message, name=f"{s.title} (continued)"), s.project,
                           hidden=bool(message))
        self.push_screen(FirstMessage(tilde(s.cwd), text), done)

    def action_fork(self) -> None:
        if s := self.selected():
            self.begin(launch.fork(s.id, s.cwd, s.title), s.project)

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
