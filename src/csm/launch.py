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


def new(project: str, worktree: bool = False, prompt: str = "", name: str = "") -> Launch:
    """`prompt` becomes the first message. It goes right after `claude`, since `-w` takes an
    optional name; a leading space keeps a prompt that starts with - from reading as an option."""
    sid = str(uuid.uuid4())
    first = [(" " + prompt) if prompt.startswith("-") else prompt] if prompt else []
    argv = [_claude(), *first, "--session-id", sid] + (["--name", name] if name else []) + (["-w"] if worktree else [])
    return Launch(project, argv, sid, "New worktree session" if worktree else "New session")


def fork(session_id: str, cwd: str, title: str) -> Launch:
    """`--session-id` may accompany --resume only together with --fork-session."""
    sid = str(uuid.uuid4())
    # Name it, or the fork keeps the original's title and the two look like duplicates.
    return Launch(cwd, [_claude(), "-r", session_id, "--fork-session", "--session-id", sid,
                        "--name", f"{title} (fork)"], sid, f"Fork of {title}")
