"""Tips about features you aren't using, shown when what you're doing is what they're for."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .usage import Usage

QUIET = 600  # seconds between any two tips
ENOUGH = 3  # once you've used a feature this often, its tip stops


@dataclass
class Signals:
    """What the app noticed you doing; a tip's trigger reads these."""
    waiting_opened_by_hand: int = 0  # opened a session that was waiting for you, without tab
    focus_then_tab: int = 0  # ctrl+\ back to the list, then tab right after
    opened_full_context: bool = False  # opened a session whose context is over 80%
    long_scroll: bool = False  # moved the cursor 20+ times to reach the session you opened
    archives_one_by_one: int = 0  # archived sessions singly, in a burst
    recap_due: bool = False  # after 5pm, an hour or more of Claude time today, no recap yet
    shown_at: float = 0.0


@dataclass(frozen=True)
class Tip:
    id: str
    feature: str  # the usage name; using it ENOUGH times retires the tip
    text: str
    when: Callable[[Signals], bool] = field(compare=False)


TIPS = [
    Tip("tab", "next_waiting", "Tip: tab opens whichever session has waited longest for you. Press it again for the next one.",
        lambda s: s.waiting_opened_by_hand >= 2),
    Tip("ctrl-bracket", "next_waiting", "Tip: ctrl+] jumps to the next waiting session from inside a session; no need to go back to the list.",
        lambda s: s.focus_then_tab >= 2),
    Tip("brief", "brief", "Tip: that session's context is over 80% full. B continues it in a fresh session from a brief.",
        lambda s: s.opened_full_context),
    Tip("filter", "filter", "Tip: / filters the list as you type (title, project, branch, #tag), and ] / [ jump between projects.",
        lambda s: s.long_scroll),
    Tip("mark", "mark", "Tip: space marks sessions; x then archives all the marked ones at once.",
        lambda s: s.archives_one_by_one >= 3),
    Tip("recap", "recap", "Tip: J writes today's recap (time, cost, PRs, what each session did) to Markdown.",
        lambda s: s.recap_due),
]


def pick(usage: Usage, signals: Signals, now: float) -> Tip | None:
    """The tip to show now, if any: at most one every QUIET seconds, each at most once a day."""
    if usage.muted or now - signals.shown_at < QUIET:
        return None
    today = usage.day()
    for tip in TIPS:
        if usage.total(tip.feature) >= ENOUGH or usage.tips.get(tip.id) == today:
            continue
        if tip.when(signals):
            return tip
    return None


def shown(tip: Tip, usage: Usage, signals: Signals, now: float) -> None:
    usage.tips[tip.id] = usage.day()
    usage.dirty = True
    signals.shown_at = now
