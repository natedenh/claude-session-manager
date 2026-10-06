"""Commands csm starts, and the sessions it started that have no transcript yet."""
from __future__ import annotations

import shlex
import shutil
import uuid
from dataclasses import dataclass


@dataclass
class Launch:
    cwd: str
    argv: list[str]
    focus_id: str  # session to highlight when the list comes back
    label: str = ""

    @property
    def command(self) -> str:
        return shlex.join(self.argv)


@dataclass
class Pending:
    """A session csm started that hasn't written a transcript (so has no title) yet."""
    id: str
    project: str
    launch: Launch


def _claude() -> str:
    return shutil.which("claude") or "claude"


def resume(session_id: str, cwd: str, label: str = "") -> Launch:
    return Launch(cwd, [_claude(), "-r", session_id], session_id, label)


def new(project: str, worktree: bool = False) -> Launch:
    sid = str(uuid.uuid4())
    argv = [_claude(), "--session-id", sid] + (["-w"] if worktree else [])
    return Launch(project, argv, sid, "New worktree session" if worktree else "New session")


def fork(session_id: str, cwd: str, title: str) -> Launch:
    """`--session-id` may accompany --resume only together with --fork-session."""
    sid = str(uuid.uuid4())
    return Launch(cwd, [_claude(), "-r", session_id, "--fork-session", "--session-id", sid], sid,
                  f"Fork of {title}")
