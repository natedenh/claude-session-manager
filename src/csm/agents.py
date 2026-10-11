"""A session's subagents: what each was asked, on which model, whether it's done, what it found.

Claude Code writes each subagent's conversation to <session>/subagents/agent-<id>.jsonl, with a
.meta.json beside it (its type, task, the model asked for, its worktree). The main transcript
records the launch (the model it actually got) and, for agents run in the background, a
task-notification when each one finishes, with the start of its result.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import data, models

RUNNING_QUIET = 600  # an unfinished agent whose file hasn't changed in this long has stopped
NOTE_RE = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)


@dataclass
class Agent:
    id: str
    task: str
    type: str
    path: str
    model: str = ""  # what it ran on
    started: float = 0.0
    last: float = 0.0  # its last record
    touched: float = 0.0  # its file's last change, for telling a running agent from a stopped one
    status: str = "running"  # running, done, failed, stopped
    result: str = ""
    weight: float = 0.0  # price-weighted tokens (see data.TOKEN_WEIGHT), for its share of the session's cost
    worktree: str = ""
    messages: int = 0

    @property
    def model_label(self) -> str:
        return models.family(self.model) or "?"

    @property
    def took(self) -> float:
        return max(0.0, self.last - self.started)


def one_line(text: str) -> str:
    """A result as one plain line: no Markdown headings, bold or code marks."""
    text = re.sub(r"^#+\s*", "", text.strip(), flags=re.M)
    text = re.sub(r"\*\*|__|`", "", text)
    return " ".join(text.split())


def _iso(ts: str | None) -> float:
    if not ts:
        return 0.0
    from datetime import datetime
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _notes(main: Path) -> dict[str, tuple[str, str]]:
    """{agent id: (status, result)} from the main transcript's task-notifications and agent results."""
    out: dict[str, tuple[str, str]] = {}
    try:
        f = open(main, errors="replace")
    except OSError:
        return out
    with f:
        for line in f:
            if "task-notification" not in line and '"agentId"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            content = d.get("content") if d.get("type") == "queue-operation" else (d.get("message") or {}).get("content")
            text = content if isinstance(content, str) else json.dumps(content) if content else ""
            for body in NOTE_RE.findall(text.replace("\\n", "\n")):
                aid = re.search(r"<task-id>([^<]+)</task-id>", body)
                status = re.search(r"<status>([^<]+)</status>", body)
                result = re.search(r"<result>(.*?)(?:</result>|$)", body, re.S)
                if aid and status:
                    out[aid.group(1).strip()] = (status.group(1).strip(), (result.group(1).strip() if result else ""))
            t = d.get("toolUseResult")
            if isinstance(t, dict) and t.get("agentId") and t.get("status") not in (None, "async_launched"):
                text = t.get("content") if isinstance(t.get("content"), str) else ""
                out.setdefault(t["agentId"], (str(t["status"]), text))
    return out


def read(main: Path, live: bool, now: float | None = None) -> list[Agent]:
    """A session's agents, oldest first. `live`: the session is running, so an unfinished agent may be too."""
    now = now or time.time()
    files = sorted((main.parent / main.stem / "subagents").glob("agent-*.jsonl"))
    if not files:
        return []
    notes = _notes(main)
    out = []
    for f in files:
        aid = f.stem.removeprefix("agent-")
        try:
            meta = json.loads(f.with_suffix(".meta.json").read_text())
        except (OSError, ValueError):
            meta = {}
        a = Agent(id=aid, task=meta.get("description") or aid, type=meta.get("agentType") or "agent", path=str(f),
                  worktree=meta.get("worktreePath") or "")
        msgs: dict = {}
        memo: dict = {}
        first = last = said = handback = None
        try:
            with open(f, errors="replace") as fh:
                for line in fh:
                    if m := data.TIMESTAMP_RE.search(line):
                        first = first or m.group(1)
                        last = m.group(1)
                    data._note_message(line, last, msgs, memo)
                    if '"type":"assistant"' in line and '"type":"text"' in line:
                        said = line
                    if '"SubagentHandback"' in line:  # the report it handed back: better than its last words
                        try:
                            for c in json.loads(line).get("message", {}).get("content") or []:
                                if isinstance(c, dict) and c.get("name") == "SubagentHandback":
                                    handback = (c.get("input") or {}).get("message") or handback
                        except (ValueError, AttributeError):
                            pass
            a.touched = f.stat().st_mtime
        except OSError:
            continue
        a.started, a.last = _iso(first), _iso(last)
        own = handback or ""
        if said and not own:  # its final words, for when the notification doesn't carry the result
            try:
                own = data.message_text(json.loads(said).get("message", {}).get("content"))
            except ValueError:
                pass
        a.weight = sum(w for *_, w in msgs.values())
        a.messages = len(msgs)
        if msgs:
            a.model = max(msgs.values())[2] or meta.get("model") or ""
        else:
            a.model = meta.get("model") or ""
        status, result = notes.get(aid, ("", ""))
        if result.startswith("This agent's report was delivered") or not result:
            result = own
        a.result = one_line(result)
        if status:
            a.status = "done" if status == "completed" else "failed" if status in ("failed", "error") else status
        elif not live or now - a.touched > RUNNING_QUIET:
            a.status = "stopped"
        out.append(a)
    out.sort(key=lambda a: a.started or a.last)
    return out


@dataclass
class Cache:
    """Agents per session, re-read only when the session's files change."""
    entries: dict[str, tuple[tuple, list[Agent]]] = field(default_factory=dict)

    def get(self, s, live: bool) -> list[Agent]:
        main = Path(s.path)
        try:  # the file itself, not the session's last full read: finish notices land in it in between
            st = main.stat()
        except OSError:
            return []
        key = (st.st_mtime, st.st_size, json.dumps(data.subagent_signature(main)), live)
        hit = self.entries.get(s.id)
        if hit and hit[0] == key:
            return hit[1]
        agents = read(main, live)
        self.entries[s.id] = (key, agents)
        return agents
