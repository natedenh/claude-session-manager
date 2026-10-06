"""Reading and writing Claude Code's on-disk session data.

Transcripts live in ~/.claude/projects/<encoded-dir>/<session-id>.jsonl, one JSON
record per line. Running processes register themselves in ~/.claude/sessions/<pid>.json.
Our own state (archived sessions, collapsed projects) and a parse cache live outside
~/.claude so we never add files to Claude's directories.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

HOME = Path.home()
WORKTREE_MARK = "/.claude/worktrees/"
CACHE_VERSION = 3  # bump whenever parse_session changes

CWD_RE = re.compile(r'"cwd":"((?:[^"\\]|\\.)*)"')
BRANCH_RE = re.compile(r'"gitBranch":"((?:[^"\\]|\\.)*)"')
TIMESTAMP_RE = re.compile(r'"timestamp":"([^"]+)"')


@dataclass
class Paths:
    claude: Path = field(default_factory=lambda: Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude"))
    cache: Path = field(default_factory=lambda: Path(os.environ.get("XDG_CACHE_HOME") or HOME / ".cache") / "csm" / "index.json")
    prs: Path = field(default_factory=lambda: Path(os.environ.get("XDG_CACHE_HOME") or HOME / ".cache") / "csm" / "prs.json")
    state: Path = field(default_factory=lambda: Path(os.environ.get("XDG_STATE_HOME") or HOME / ".local" / "state") / "csm" / "state.json")
    trash: Path = HOME / ".Trash"
    # Claude desktop's data dirs ("Claude", "Claude-3p", …) live here.
    desktop: Path = HOME / "Library" / "Application Support"

    @property
    def projects(self) -> Path:
        return self.claude / "projects"

    @property
    def live(self) -> Path:
        return self.claude / "sessions"


@dataclass
class Session:
    id: str
    path: str
    title: str
    project: str  # directory the session is grouped under; worktrees fold into their repo
    cwd: str  # directory to run `claude -r` from
    branch: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    worktree: bool = False
    worktrees: list[str] = field(default_factory=list)  # worktree directories its cwds used
    cost: float | None = None
    started: str | None = None  # ISO timestamp of the first record
    mtime: float = 0.0
    size: int = 0

    @property
    def project_name(self) -> str:
        return os.path.basename(self.project) or self.project


@dataclass
class LiveSession:
    pid: int
    session_id: str
    status: str
    entrypoint: str
    name: str | None = None
    cwd: str | None = None
    host_session_id: str | None = None  # the desktop app's own id for the session


@dataclass
class Message:
    role: str  # "user", "assistant", or "tools" (a run of tool calls)
    text: str


def encode_dir(path: str) -> str:
    """The directory name Claude Code stores a project's transcripts under."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def message_text(content) -> str | None:
    """Plain text of a message's content, or None if it has none (tool calls/results)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
        return "\n".join(parts) if parts else None
    return None


def is_prompt(text: str | None) -> bool:
    """User records also carry slash-command wrappers and system injections, which start with '<'."""
    return bool(text and text.strip()) and not text.lstrip().startswith("<")


def worktree_root(cwd: str) -> str:
    """<repo>/.claude/worktrees/<name>/sub/dir -> <repo>/.claude/worktrees/<name>"""
    repo, _, rest = cwd.partition(WORKTREE_MARK)
    return repo + WORKTREE_MARK + rest.split("/")[0]


def parse_session(path: Path) -> Session | None:
    custom = ai = first_prompt = pr = pr_url = branch = cost = started = relocated = None
    cwds: list[str] = []  # distinct cwds, in first-seen order
    st = path.stat()
    try:
        with open(path, errors="replace") as f:
            for line in f:
                if started is None and (m := TIMESTAMP_RE.search(line)):
                    started = m.group(1)
                if '"cwd":"' in line and (m := CWD_RE.search(line)):
                    cwd = json.loads(f'"{m.group(1)}"')
                    if cwd not in cwds:
                        cwds.append(cwd)
                    if m := BRANCH_RE.search(line):
                        branch = json.loads(f'"{m.group(1)}"')
                # Cheap prefilter; session files can be tens of MB.
                if not ('"custom-title"' in line or '"ai-title"' in line or '"pr-link"' in line
                        or '"cost-state"' in line or '"relocated"' in line
                        or (first_prompt is None and '"type":"user"' in line)):
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                t = d.get("type")
                if t == "custom-title":
                    custom = d.get("customTitle") or custom  # last one wins, so renames stick
                elif t == "ai-title":
                    ai = d.get("aiTitle") or ai
                elif t == "pr-link":
                    pr, pr_url = d.get("prNumber") or pr, d.get("prUrl") or pr_url
                elif t == "relocated":
                    relocated = d.get("relocatedCwd") or relocated
                elif t == "cost-state":
                    cost = d.get("totalCostUSD", cost)
                elif t == "user" and first_prompt is None and not d.get("isMeta"):
                    text = message_text(d.get("message", {}).get("content"))
                    if is_prompt(text):
                        first_prompt = text
    except OSError:
        return None
    title = custom or ai or first_prompt
    if not title:
        return None  # empty or aborted session
    title = " ".join(title.split())

    # `claude -r` looks the id up under the current directory's project folder, so the cwd
    # that maps to the folder this transcript lives in is both where it was launched and
    # where to resume from. Later cwds are just the shell wandering, so they don't regroup it.
    home = next((c for c in cwds if encode_dir(c) == path.parent.name), None)
    if relocated and encode_dir(relocated) == path.parent.name:
        home = relocated
    where = relocated or home or (cwds[0] if cwds else None)
    if where is None:  # fall back to decoding the dir name (lossy)
        where = "/" + re.sub(r"--claude-worktrees-.*", "", path.parent.name).lstrip("-").replace("-", "/")
    project = where.split(WORKTREE_MARK)[0]
    worktree = any(c.startswith(project + WORKTREE_MARK) for c in [where, *cwds])
    worktrees = list(dict.fromkeys(worktree_root(c) for c in [where, *cwds] if WORKTREE_MARK in c))
    cwd = next((c for c in (home, where, project) if c and os.path.isdir(c)), project)

    return Session(
        id=path.stem, path=str(path), title=title, project=project, cwd=cwd, branch=branch,
        pr_number=pr, pr_url=pr_url, worktree=worktree, worktrees=worktrees, cost=cost, started=started,
        mtime=st.st_mtime, size=st.st_size,
    )


def load_sessions(paths: Paths) -> list[Session]:
    """All sessions, newest first. Unchanged files are served from the cache."""
    try:
        cache = json.loads(paths.cache.read_text())
        if cache.get("version") != CACHE_VERSION:
            cache = {}
    except (OSError, ValueError):
        cache = {}
    old = cache.get("files", {})
    new, out, dirty = {}, [], False
    for f in paths.projects.glob("*/*.jsonl"):
        try:
            st = f.stat()
        except OSError:
            continue
        key = str(f)
        entry = old.get(key)
        if not entry or entry["mtime"] != st.st_mtime or entry["size"] != st.st_size:
            s = parse_session(f)
            entry = {"mtime": st.st_mtime, "size": st.st_size, "session": asdict(s) if s else None}
            dirty = True
        new[key] = entry
        if entry["session"]:
            out.append(Session(**entry["session"]))
    if dirty or len(new) != len(old):
        _write_json(paths.cache, {"version": CACHE_VERSION, "files": new})
    out.sort(key=lambda s: -s.mtime)
    # Relocating a session (e.g. into a worktree) copies its transcript, so one id can
    # live in two folders. The newest copy is the one still being written.
    seen: set[str] = set()
    return [s for s in out if not (s.id in seen or seen.add(s.id))]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass
class DesktopRecord:
    local_id: str  # the desktop app's id, "local_…"
    archived: bool


_desktop_cache: dict[str, tuple[float, DesktopRecord | None, str | None]] = {}


def load_desktop(paths: Paths) -> dict[str, DesktopRecord]:
    """Claude desktop's per-session records, keyed by transcript (CLI) session id.

    Each lives at <data dir>/claude-code-sessions/<account>/<org>/local_<id>.json. They can
    be large, so a file is only re-read when its mtime changes.
    """
    out = {}
    for f in paths.desktop.glob("Claude*/claude-code-sessions/*/*/local_*.json"):
        key = str(f)
        try:
            mtime = f.stat().st_mtime
            cached = _desktop_cache.get(key)
            if not cached or cached[0] != mtime:
                d = json.loads(f.read_text())
                rec = DesktopRecord(local_id=d.get("sessionId") or f.stem, archived=d.get("isArchived") is True)
                cached = _desktop_cache[key] = (mtime, rec, d.get("cliSessionId"))
        except (OSError, ValueError, AttributeError):
            continue
        _, rec, cli_id = cached
        if rec and cli_id:
            out[cli_id] = rec
    return out


def load_live(paths: Paths) -> dict[str, LiveSession]:
    """Running Claude processes, keyed by session id."""
    live = {}
    for f in paths.live.glob("*.json"):
        try:
            d = json.loads(f.read_text())
            pid = int(d["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if d.get("sessionId") and _alive(pid):
            live[d["sessionId"]] = LiveSession(
                pid=pid, session_id=d["sessionId"], status=d.get("status") or "running",
                entrypoint=d.get("entrypoint") or d.get("kind") or "", name=d.get("name"), cwd=d.get("cwd"),
                host_session_id=d.get("hostSessionId"),
            )
    return live


def transcript(path: str | Path, limit: int = 30, tail_bytes: int = 4_000_000) -> list[Message]:
    """The last `limit` conversation turns, read from the tail of the file."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - tail_bytes))
        lines = f.read().split(b"\n")
    if size > tail_bytes:
        lines = lines[1:]  # first line is partial
    out: list[Message] = []
    for raw in lines:
        if b'"type":"user"' not in raw and b'"type":"assistant"' not in raw:
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        t = d.get("type")
        if t not in ("user", "assistant") or d.get("isSidechain") or d.get("isMeta"):
            continue
        content = d.get("message", {}).get("content")
        text = message_text(content)
        if t == "assistant" and isinstance(content, list) and any(
                isinstance(p, dict) and p.get("type") == "tool_use" for p in content):
            n = sum(1 for p in content if isinstance(p, dict) and p.get("type") == "tool_use")
            if out and out[-1].role == "tools":
                out[-1].text = str(int(out[-1].text) + n)
            else:
                out.append(Message("tools", str(n)))
        if t == "user" and not is_prompt(text):
            continue
        if not text or not text.strip():
            continue
        text = text.strip()
        if t == "assistant" and out and out[-1].role == "assistant":
            out[-1].text += "\n\n" + text
        else:
            out.append(Message(t, text))
    return out[-limit:]


def search(sessions: list[Session], query: str, cancelled: Callable[[], bool] = lambda: False,
           max_hits: int = 3, context: int = 70) -> dict[str, list[str]]:
    """Case-insensitive search of message text. Returns {session id: snippets}."""
    needle = query.lower()
    bneedle = needle.encode()
    hits: dict[str, list[str]] = {}
    for s in sessions:
        if cancelled():
            break
        try:
            data = Path(s.path).read_bytes()
        except OSError:
            continue
        if bneedle not in data.lower():
            continue
        snippets = []
        for raw in data.split(b"\n"):
            if bneedle not in raw.lower():
                continue
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if d.get("type") not in ("user", "assistant") or d.get("isSidechain"):
                continue
            text = message_text(d.get("message", {}).get("content"))
            if not text or (d["type"] == "user" and not is_prompt(text)):
                continue
            i = text.lower().find(needle)
            if i < 0:
                continue
            start, end = max(0, i - context), i + len(needle) + context
            snippet = " ".join(text[start:end].split())
            snippets.append(("…" if start else "") + snippet + ("…" if end < len(text) else ""))
            if len(snippets) >= max_hits:
                break
        if snippets:
            hits[s.id] = snippets
    return hits


def rename(session: Session, title: str) -> None:
    """Append a custom-title record, the same thing `/rename` does."""
    record = json.dumps({"type": "custom-title", "customTitle": title, "sessionId": session.id},
                        separators=(",", ":"))
    with open(session.path, "rb+") as f:
        f.seek(0, os.SEEK_END)
        if f.tell():
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                f.write(b"\n")
        f.write(record.encode() + b"\n")


def trash(session: Session, paths: Paths) -> Path:
    """Move the transcript (and its subagent folder, if any) to the Trash."""
    src = Path(session.path)
    paths.trash.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = paths.trash / f"claude-session-{src.stem}-{stamp}.jsonl"
    shutil.move(src, dest)
    extra = src.with_suffix("")
    if extra.is_dir():
        shutil.move(extra, paths.trash / f"claude-session-{src.stem}-{stamp}")
    return dest


class State:
    """csm's own persisted state."""

    def __init__(self, path: Path):
        self.path = path
        try:
            d = json.loads(path.read_text())
        except (OSError, ValueError):
            d = {}
        self.archived: set[str] = set(d.get("archived", []))
        self.collapsed: set[str] = set(d.get("collapsed", []))
        self.pinned: set[str] = set(d.get("pinned", []))
        self.flat: bool = bool(d.get("flat", False))

    def save(self) -> None:
        _write_json(self.path, {"archived": sorted(self.archived), "collapsed": sorted(self.collapsed),
                                "pinned": sorted(self.pinned), "flat": self.flat})


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, path)
