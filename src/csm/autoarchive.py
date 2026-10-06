"""Auto-archive rule: hide sessions whose PR merged long ago or that have gone quiet. Derived, never stored per session."""
from __future__ import annotations

DEFAULT = {"enabled": False, "merged_days": 7, "idle_days": 30}
DAY = 86400


def normalize(raw) -> dict:
    """A rule dict from possibly old, partial or junk saved data."""
    rule = dict(DEFAULT)
    if isinstance(raw, dict):
        rule["enabled"] = bool(raw.get("enabled", False))
        for k in ("merged_days", "idle_days"):
            v = raw.get(k)
            if isinstance(v, int) and not isinstance(v, bool) and v >= 1:
                rule[k] = v
    return rule


def reason(rule: dict, mtime: float, merged_at: float | None, now: float, exempt: bool) -> str | None:
    """Why the rule archives a session, or None. `exempt`: live, pinned or kept."""
    if not rule["enabled"] or exempt:
        return None
    if merged_at is not None and now - merged_at > rule["merged_days"] * DAY:
        return f"auto: PR merged {int((now - merged_at) // DAY)}d ago"
    if now - mtime > rule["idle_days"] * DAY:
        return f"auto: idle {int((now - mtime) // DAY)}d"
    return None
