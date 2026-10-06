"""Status of the pull requests that sessions are linked to, via the `gh` CLI.

Results are cached in a JSON file keyed by PR URL. Open PRs go stale after a few
minutes; merged and closed ones are final, so they're only re-checked daily.
"""
from __future__ import annotations

import json
from datetime import datetime
import subprocess
import time
from dataclasses import asdict, dataclass

from .data import Paths, _write_json

OPEN_TTL = 5 * 60
FINAL_TTL = 24 * 60 * 60
FIELDS = "state,isDraft,reviewDecision,statusCheckRollup,mergedAt"

BAD_CONCLUSIONS = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE", "ERROR"}
OK_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
REVIEWS = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes_requested", "REVIEW_REQUIRED": "review_required"}


@dataclass
class PRStatus:
    state: str  # open, draft, merged, closed
    checks: str  # passing, failing, pending, none
    review: str | None  # approved, changes_requested, review_required
    passed: int
    total: int
    fetched_at: float = 0.0
    merged_at: float | None = None

    @property
    def final(self) -> bool:
        return self.state in ("merged", "closed")

    def stale(self, now: float | None = None) -> bool:
        age = (now or time.time()) - self.fetched_at
        return age > (FINAL_TTL if self.final else OPEN_TTL)


def check_result(c: dict) -> str:
    """One rollup entry -> passing, failing or pending. CheckRuns and StatusContexts differ in shape."""
    if "state" in c and "status" not in c:  # StatusContext
        s = (c["state"] or "").upper()
        return "passing" if s == "SUCCESS" else "failing" if s in ("FAILURE", "ERROR") else "pending"
    if (c.get("status") or "").upper() != "COMPLETED":
        return "pending"
    conclusion = (c.get("conclusion") or "").upper()
    return "passing" if conclusion in OK_CONCLUSIONS else "failing" if conclusion in BAD_CONCLUSIONS else "pending"


def merge_time(value) -> float | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def parse(payload: dict, now: float | None = None) -> PRStatus:
    raw = (payload.get("state") or "").upper()
    state = ("merged" if raw == "MERGED" or payload.get("mergedAt") else "closed" if raw == "CLOSED"
             else "draft" if payload.get("isDraft") else "open")
    results = [check_result(c) for c in payload.get("statusCheckRollup") or []]
    checks = ("none" if not results else "failing" if "failing" in results
              else "pending" if "pending" in results else "passing")
    return PRStatus(state, checks, REVIEWS.get(payload.get("reviewDecision") or ""),
                    results.count("passing"), len(results), now or time.time(),
                    merge_time(payload.get("mergedAt")) if state == "merged" else None)


def fetch(pr_url: str) -> PRStatus | None:
    """Ask gh; None when it's missing, logged out, offline or returns something unexpected."""
    try:
        r = subprocess.run(["gh", "pr", "view", pr_url, "--json", FIELDS],
                           capture_output=True, text=True, timeout=30)
        if r.returncode:
            return None
        return parse(json.loads(r.stdout))
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return None


def load(paths: Paths) -> dict[str, PRStatus]:
    try:
        raw = json.loads(paths.prs.read_text())
        return {url: PRStatus(**v) for url, v in raw.items()}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def cached(paths: Paths, pr_url: str) -> PRStatus | None:
    return load(paths).get(pr_url)


def refresh(paths: Paths, pr_urls: list[str]) -> bool:
    """Fetch the stale ones in order, saving after each. True if anything changed."""
    statuses = load(paths)
    changed = False
    for url in pr_urls:
        if (old := statuses.get(url)) and not old.stale():
            continue
        if (new := fetch(url)) is not None:
            statuses[url] = new
            _write_json(paths.prs, {u: asdict(s) for u, s in statuses.items()})
            changed = True
    return changed
