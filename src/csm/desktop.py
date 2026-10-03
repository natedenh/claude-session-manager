"""Opening sessions in the Claude desktop app through its claude:// URL scheme.

The app handles claude://code/continue?session=<id>, where <id> is its own session id
("local_…"). That id is only recorded in the live-session file a running desktop session
writes (hostSessionId), so only sessions open in the app right now can be targeted.
"""
from __future__ import annotations

import re
import subprocess

from .data import LiveSession

SESSION_ID = re.compile(r"^local_[A-Za-z0-9-]{1,64}$")  # the pattern the app accepts


def can_open(live: LiveSession) -> bool:
    return live.entrypoint.startswith("claude-desktop") and bool(
        live.host_session_id and SESSION_ID.match(live.host_session_id))


def open_session(live: LiveSession) -> None:
    subprocess.run(["open", f"claude://code/continue?session={live.host_session_id}"],
                   check=True, capture_output=True)
