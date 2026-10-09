"""Claude desktop's routines (scheduled tasks), read-only.

The desktop app keeps them in <data dir>/claude-code-sessions/<account>/<org>/scheduled-tasks.json
(Cowork's in local-agent-mode-sessions/), each pointing at a SKILL.md with the instructions,
usually ~/.claude/scheduled-tasks/<id>/SKILL.md. The app runs them; each run is an ordinary
desktop session whose record names its scheduledTaskId. csm only reads all of this.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .data import Paths

DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


@dataclass
class Routine:
    id: str
    name: str
    kind: str  # "code" or "cowork"
    enabled: bool
    cron: str | None = None  # repeating
    fire_at: float | None = None  # once
    cwd: str = ""
    description: str = ""
    instructions: str = ""
    path: str = ""
    created: float = 0.0
    last_run: float | None = None
    runs: list = field(default_factory=list)  # data.DesktopRecord, newest first; filled in by the app

    @property
    def status(self) -> str:
        if self.cron:
            return "active" if self.enabled else "paused"
        if self.last_run and (not self.fire_at or self.last_run >= self.fire_at - 60):
            return "completed"
        return "scheduled" if self.enabled else "off"

    def next_run(self, now: datetime | None = None) -> datetime | None:
        now = now or datetime.now()
        if not self.enabled:
            return None
        if self.cron:
            return next_fire(self.cron, now)
        if self.fire_at and self.status == "scheduled":
            return datetime.fromtimestamp(self.fire_at)
        return None

    @property
    def schedule(self) -> str:
        if self.cron:
            return describe(self.cron)
        if self.fire_at:
            return "once, " + when(datetime.fromtimestamp(self.fire_at))
        return "manual"


def iso_ts(value) -> float | None:
    if isinstance(value, (int, float)):
        return value / 1000 if value > 1e11 else float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def read_skill(path: str) -> tuple[str, str]:
    """(description, body) from a SKILL.md with front matter."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", ""
    desc = ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                if line.startswith("description:"):
                    desc = line.split(":", 1)[1].strip().strip('"')
            text = text[end + 4:]
    return desc, text.strip()


def load(paths: Paths) -> list[Routine]:
    out = []
    for kind, pattern in (("code", "Claude*/claude-code-sessions/*/*/scheduled-tasks.json"),
                          ("cowork", "Claude*/local-agent-mode-sessions/*/*/scheduled-tasks.json")):
        for f in paths.desktop.glob(pattern):
            try:
                tasks = json.loads(f.read_text()).get("scheduledTasks") or []
            except (OSError, ValueError, AttributeError):
                continue
            for t in tasks:
                if not isinstance(t, dict) or not t.get("id"):
                    continue
                desc, body = read_skill(t.get("filePath") or "")
                out.append(Routine(
                    id=t["id"], name=t.get("displayName") or t["id"], kind=kind, enabled=t.get("enabled") is True,
                    cron=t.get("cronExpression") or None, fire_at=iso_ts(t.get("fireAt")), cwd=t.get("cwd") or "",
                    description=desc, instructions=body, path=t.get("filePath") or "",
                    created=iso_ts(t.get("createdAt")) or 0.0, last_run=iso_ts(t.get("lastRunAt"))))
    out.sort(key=lambda r: (r.status not in ("active", "scheduled"), r.name.lower()))
    return out


# ---- cron, in local time like the desktop app -------------------------------------

def _field(spec: str, lo: int, hi: int) -> set[int] | None:
    """The values a cron field allows; None for '*' (anything)."""
    if spec == "*":
        return None
    out: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
        else:
            a = b = int(part)
            if step > 1:
                b = hi
        out.update(range(a, b + 1, step))
    return out


def parse(cron: str) -> tuple | None:
    parts = cron.split()
    if len(parts) != 5:
        return None
    try:
        minute, hour, dom, month, dow = (_field(p, lo, hi) for p, (lo, hi) in
                                         zip(parts, ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))))
    except ValueError:
        return None
    if dow is not None:
        dow = {d % 7 for d in dow}  # 7 is Sunday too
    return minute, hour, dom, month, dow


def next_fire(cron: str, after: datetime) -> datetime | None:
    if not (p := parse(cron)):
        return None
    minute, hour, dom, month, dow = p
    start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for day_offset in range(367):
        day = (start + timedelta(days=day_offset)).date()
        if month is not None and day.month not in month:
            continue
        # Standard cron: when both day fields are restricted, either may match.
        dom_ok = dom is None or day.day in dom
        dow_ok = dow is None or (day.isoweekday() % 7) in dow
        if not ((dom_ok or dow_ok) if dom is not None and dow is not None else (dom_ok and dow_ok)):
            continue
        for h in sorted(hour) if hour is not None else range(24):
            for m in sorted(minute) if minute is not None else range(60):
                t = datetime(day.year, day.month, day.day, h, m)
                if t >= start:
                    return t
    return None


def clock(h: int, m: int) -> str:
    return datetime(2000, 1, 1, h, m).strftime("%-I:%M %p")


def describe(cron: str) -> str:
    """Plain words for the common shapes; the cron itself otherwise."""
    parts = cron.split()
    if len(parts) != 5:
        return cron
    mi, ho, dom, mo, dw = parts
    if mo != "*" or dom != "*":
        return f"cron {cron}"
    if re.fullmatch(r"\*/\d+", mi) and ho == "*" and dw == "*":
        return f"every {mi[2:]} minutes"
    if mi.isdigit() and ho == "*" and dw == "*":
        return "every hour"
    if mi.isdigit() and re.fullmatch(r"\*/\d+", ho) and dw == "*":
        n = int(ho[2:])
        return "every hour" if n == 1 else f"every {n} hours"
    if mi.isdigit() and ho.isdigit():
        at = clock(int(ho), int(mi))
        if dw == "*":
            return f"daily at {at}"
        if dw == "1-5":
            return f"weekdays at {at}"
        if dw.isdigit():
            return f"{DAYS[int(dw) % 7]}s at {at}"
    return f"cron {cron}"


def when(t: datetime, now: datetime | None = None) -> str:
    now = now or datetime.now()
    if t.date() == now.date():
        day = "today"
    elif t.date() == (now + timedelta(days=1)).date():
        day = "tomorrow"
    elif t.date() == (now - timedelta(days=1)).date():
        day = "yesterday"
    else:
        day = t.strftime("%b %-d")
    return f"{day} at {t.strftime('%-I:%M %p')}"


def until(t: datetime, now: datetime | None = None) -> str:
    """"in 3h", "in 25m", "in 2d"."""
    s = (t - (now or datetime.now())).total_seconds()
    for unit, sec in (("d", 86400), ("h", 3600)):
        if s >= sec:
            return f"in {int(s // sec)}{unit}"
    return f"in {max(1, int(s // 60))}m"
