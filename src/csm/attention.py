"""What needs you: sessions waiting, asking permission or stuck; the tab queue; when-idle
actions; and the usage log and tips. Mixed into the app (csm.app.CSM)."""
from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime

from textual import events, work

from . import hooks, notify, tips
from .data import LiveSession
from .dialogs import Prompt, WhenIdle
from .fmt import waited


class AttentionMixin:
    """Waiting, permission and stuck state, and acting on it."""

    def note_action(self, action: str) -> None:
        """Count features used, and notice habits a tip could help with."""
        name, now = action.removeprefix("app."), time.time()
        if name in ("cursor_down", "cursor_up", "page_down", "page_up"):
            self.moves += 1
            return
        feature = self.usage.record(action)
        if feature == "next_waiting" and self.last_action[0] == "back" and now - self.last_action[1] < 5:
            self.signals.focus_then_tab += 1
        if feature == "archive" and not self.marked:
            self.archived_at = [t for t in self.archived_at if now - t < 120] + [now]
            self.signals.archives_one_by_one = len(self.archived_at)
        self.last_action = (feature or name, now)

    def on_app_focus(self, event: events.AppFocus) -> None:
        self.last_action = ("back", time.time())  # back in the list from a session pane

    def offer_tip(self) -> None:
        day = datetime.now()
        today = sum(s.active.get(day.date().isoformat(), 0) for s in self.sessions)
        self.signals.recap_due = day.hour >= 17 and today >= 60 and self.usage.last("recap") != self.usage.day()
        if tip := tips.pick(self.usage, self.signals, time.time()):
            self.notify(f"{tip.text}\n(ctrl+t turns tips off)", timeout=12)
            tips.shown(tip, self.usage, self.signals, time.time())

    def action_toggle_tips(self) -> None:
        self.usage.muted = not self.usage.muted
        self.usage.dirty = True
        self.usage.save(force=True)
        self.notify("Tips off; ctrl+t turns them back on" if self.usage.muted else "Tips on", timeout=3)

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

    def track_stuck(self, live: dict[str, LiveSession]) -> None:
        """Busy sessions whose transcript hasn't changed for a while. Long builds look like this too."""
        if self.stuck_minutes <= 0:
            return
        paths = {s.id: s for s in self.sessions}
        stuck = {}
        for sid, l in live.items():
            if l.status == "idle" or sid in self.permission or not (s := paths.get(sid)):
                continue
            try:
                last = os.stat(s.path).st_mtime
            except OSError:
                continue
            if time.time() - last >= self.stuck_minutes * 60:
                stuck[sid] = last
        for sid in stuck.keys() - self.stuck.keys():  # once each time it goes quiet
            if self.notifications and sid != self.shown_id:
                self.send_notification(f"{paths[sid].title} looks stuck: nothing written for {waited(stuck[sid])}")
        self.stuck = stuck

    def attention(self) -> dict[str, float]:
        """Sessions that need you -> since when."""
        return {**self.waiting, **self.permission_at}

    def labels(self) -> dict[str, str]:
        return {sid: waited(t) for sid, t in {**self.stuck, **self.attention()}.items()}

    @work(thread=True, group="notify")
    def send_notification(self, message: str) -> None:
        notify.send(message)  # may wait on tmux; keep it off the UI thread

    def clear_attention(self, sid: str) -> None:
        self.waiting.pop(sid, None)
        self.permission.pop(sid, None)
        self.permission_at.pop(sid, None)
        if sid in self.hook_notified:
            self.hook_seen[sid] = self.hook_notified[sid]
        self.hook_waiting.discard(sid)

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
        self.via_tab = True
        self.resume(s)
        self.rebuild()

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
