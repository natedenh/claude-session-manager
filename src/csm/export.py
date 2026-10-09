"""Exporting a session's full transcript as a Markdown file."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .data import Session, is_prompt, message_text

UNSAFE_RE = re.compile(r'[\x00-\x1f/\\:*?"<>|]+')
SESSION_ID_RE = re.compile(r'^session_id: "([^"]*)"', re.M)
MAX_TITLE = 80


def filename(s: Session) -> str:
    try:  # `started` is UTC; name the file by the local date the session began
        when = datetime.fromisoformat(s.started.replace("Z", "+00:00")).astimezone()
    except (AttributeError, ValueError):
        when = datetime.fromtimestamp(s.mtime)
    date = when.strftime("%Y-%m-%d")
    title = " ".join(UNSAFE_RE.sub(" ", s.title).split()).lstrip(".")[:MAX_TITLE].rstrip() or "session"
    return f"{date} {title}.md"


def conversation(path: str) -> list[tuple[str, str]]:
    """Every turn as (role, text); role is "user", "assistant" or "tools" (names, comma separated)."""
    out: list[tuple[str, str]] = []
    with open(path, errors="replace") as f:
        for line in f:
            if '"type":"user"' not in line and '"type":"assistant"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            t = d.get("type")
            if t not in ("user", "assistant") or d.get("isSidechain") or d.get("isMeta"):
                continue
            content = d.get("message", {}).get("content")
            if t == "user":
                text = message_text(content)
                if is_prompt(text):
                    out.append(("user", text.strip()))
                continue
            parts = content if isinstance(content, list) else []
            for p in parts:
                if not isinstance(p, dict):
                    continue
                if p.get("type") == "tool_use":
                    out.append(("tools", p.get("name") or "tool"))
                elif p.get("type") == "text" and (p.get("text") or "").strip():
                    out.append(("assistant", p["text"].strip()))
            if isinstance(content, str) and content.strip():
                out.append(("assistant", content.strip()))
    return out


def _tool_note(names: list[str]) -> str:
    unique = list(dict.fromkeys(names))
    n = len(names)
    return f"_Ran {n} tool call{'s' * (n != 1)}: {', '.join(unique)}_"


def render(s: Session) -> str:
    front = {
        "title": s.title, "session_id": s.id, "project": s.project, "cwd": s.cwd, "branch": s.branch,
        "pr": s.pr_url, "started": s.started,
        "last_active": datetime.fromtimestamp(s.mtime).astimezone().isoformat(timespec="seconds"),
        "cost": None if s.cost is None else round(s.cost, 2),
    }
    lines = ["---"]
    lines += [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in front.items() if v is not None]
    lines += ["---", "", f"# {s.title}", ""]
    blocks: list[str] = []
    tools: list[str] = []
    last = None  # role of the last section written
    for role, text in conversation(s.path) + [("end", "")]:
        if role == "tools":
            tools.append(text)
            continue
        if tools:
            blocks.append(_tool_note(tools))
            tools = []
        if role == "end":
            break
        if role == "user":
            blocks.append(f"## You\n\n{text}")
        elif last == "assistant":  # text split around a tool run stays in one section
            blocks.append(text)
        else:
            blocks.append(f"## Claude\n\n{text}")
        last = role
    return "\n".join(lines) + "\n" + "\n\n".join(blocks) + "\n"


def _owner(path: Path) -> str | None:
    try:
        with open(path, errors="replace") as f:
            m = SESSION_ID_RE.search(f.read(4096))
    except OSError:
        return None
    return m.group(1) if m else None


def export(s: Session, directory: Path) -> Path:
    """Write the session into `directory`. Re-exporting replaces the file; another session's file is kept."""
    directory.mkdir(parents=True, exist_ok=True)
    name = filename(s)
    stem = name[:-3]
    dest, n = directory / name, 1
    while dest.exists() and _owner(dest) != s.id:
        n += 1
        dest = directory / f"{stem} ({n}).md"
    dest.write_text(render(s), encoding="utf-8")
    return dest


def reveal(files: list[Path]) -> None:
    """Show the exported file in Finder (or the folder, for several)."""
    if not files:
        return
    if sys.platform == "darwin":
        cmd = ["open", "-R", str(files[0])] if len(files) == 1 else ["open", str(files[0].parent)]
    else:  # no "reveal" elsewhere: open the folder
        cmd = ["xdg-open", str(files[0].parent)]
    try:
        subprocess.run(cmd, check=False)
    except OSError:
        pass
