"""Formatting shared by the list, the preview and the dialogs: times, PR and git markers."""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

from rich.text import Text

from . import data, models, prs
from .data import Session


def git_flags(st: tuple[int, int, int] | None) -> Text:
    """±uncommitted files, ↑commits to push, ↓commits to pull, after a project's name."""
    if not st:
        return Text()
    dirty, ahead, behind = st
    return Text.assemble(*[("  " + t, style) for t, style, n in
                           ((f"±{dirty}", "warn", dirty), (f"↑{ahead}", "cyan", ahead), (f"↓{behind}", "dim", behind)) if n])


SPARK_DAYS = 7
SPARKS = "▁▂▃▄▅▆▇█"


def last_days(n: int = SPARK_DAYS) -> list[str]:
    """The last n local dates, oldest first, as stored in Session.active."""
    today = datetime.now().date()
    return [(today - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def sparkline(minutes: list[int], top: int) -> Text:
    """One bar per day, scaled to `top`; days with any activity get at least the lowest bar."""
    if not any(minutes):
        return Text()
    bars = "".join(" " if not m else SPARKS[max(0, min(len(SPARKS) - 1, round(m / top * (len(SPARKS) - 1))))]
                   for m in minutes)
    return Text("  " + bars, style="dim")


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
    model = f"{models.label(s.context_model)} · " if s.context_model else ""
    return Text(f"{model}context {'▰' * filled}{'▱' * (10 - filled)} {frac:.0%} · "
                f"{s.context_tokens // 1000}k of {'1M' if window >= 1_000_000 else f'{window // 1000}k'} tokens",
                style=context_style(frac))


def tilde(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home) else path
