"""Scripting the running Ghostty (1.3+) through its AppleScript dictionary."""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

from .data import LiveSession, Session

APP = "/Applications/Ghostty.app"

# argv: working directory, command line, "tab" or "window"
OPEN = """
on run argv
  tell application id "com.mitchellh.ghostty"
    set cfg to new surface configuration
    set initial working directory of cfg to item 1 of argv
    set command of cfg to item 2 of argv
    if item 3 of argv is "tab" and (count of windows) > 0 then
      new tab in front window with configuration cfg
    else
      new window with configuration cfg
    end if
    activate
  end tell
end run
"""

# `tab` is a class in Ghostty's dictionary, so the separator is spelled out.
LIST = """
tell application id "com.mitchellh.ghostty"
  set sep to character id 9
  set out to ""
  repeat with t in terminals
    set out to out & (id of t) & sep & (working directory of t) & sep & (name of t) & linefeed
  end repeat
  return out
end tell
"""

# argv: terminal id
FOCUS = """
on run argv
  tell application id "com.mitchellh.ghostty"
    repeat with t in terminals
      if (id of t as text) is item 1 of argv then
        focus t
        activate
        return "ok"
      end if
    end repeat
  end tell
  return "missing"
end run
"""


@dataclass
class Terminal:
    id: str
    cwd: str
    title: str


def running() -> bool:
    """Is Ghostty up? Scripting it otherwise would launch it."""
    return os.path.isdir(APP) and subprocess.run(["pgrep", "-xq", "ghostty"]).returncode == 0


def _osascript(script: str, *args: str) -> str:
    r = subprocess.run(["osascript", "-", *args], input=script, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or "osascript failed")
    return r.stdout.strip()


def open_session(cwd: str, command: str, where: str) -> None:
    _osascript(OPEN, cwd, command, where)


def terminals() -> list[Terminal]:
    out = []
    for line in _osascript(LIST).splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            out.append(Terminal(*parts))
    return out


def focus(terminal_id: str) -> bool:
    return _osascript(FOCUS, terminal_id) == "ok"


def _title_matches(title: str, names: set[str]) -> bool:
    """Claude prefixes the tab title with a spinner glyph (✳, ◐, …); long titles may be cut off."""
    bare = re.sub(r"^[^\w~/]+", "", title).strip()
    if bare.endswith("…"):
        bare = bare[:-1].strip()
        return bool(bare) and any(n.startswith(bare) for n in names)
    return bare in names


def find(s: Session, live: LiveSession, terms: list[Terminal]) -> Terminal | None:
    """The Ghostty terminal running a live session, matched by title, then directory."""
    names = {n for n in (s.title, live.name) if n}
    matches = [t for t in terms if _title_matches(t.title, names)]
    if len(matches) > 1:
        matches = [t for t in matches if t.cwd in (live.cwd, s.cwd) or t.cwd.startswith(s.project)] or matches
    if len(matches) == 1:
        return matches[0]
    # No usable title: accept a lone claude-looking terminal (spinner-glyph title) in its directory.
    glyph = [t for t in terms if t.cwd == (live.cwd or s.cwd) and t.title[:1] and not t.title[0].isalnum()
             and t.title[0] not in "~/"]
    return glyph[0] if len(glyph) == 1 else None
