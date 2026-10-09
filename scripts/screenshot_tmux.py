"""docs/screenshots/sidebar.svg: csm beside a real Claude Code session, in light mode.

Stages it for real on a throwaway tmux server, with the made-up sessions from screenshots.py:
csm runs as the sidebar, opens the featured session, and Claude Code resumes it in the pane on
the right. Resuming only draws the conversation, so no model is called. Both panes are captured
with their colors and joined into one SVG.

    uv run python scripts/screenshot_tmux.py
"""
from __future__ import annotations

import io
import json
import os
import pwd
import shutil
import subprocess
import sys
import time
from pathlib import Path

REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)  # before screenshots.py swaps HOME for a made-up one
sys.path.insert(0, str(Path(__file__).resolve().parent))
import screenshots as demo  # noqa: E402

from rich.console import Console  # noqa: E402
from rich.terminal_theme import TerminalTheme  # noqa: E402
from rich.text import Text  # noqa: E402

SOCKET = "csm-screenshot"
SCRUB = [" · Amazon Bedrock"]  # Claude Code's header names the provider it borrowed from your settings
WIDTH, HEIGHT = 200, 46
CONF = demo.ROOT / "src" / "csm" / "tmux.conf"
# A light terminal palette, close to what light themes in Ghostty and GitHub use.
LIGHT = TerminalTheme(
    (255, 255, 255), (36, 41, 47),
    [(36, 41, 47), (207, 34, 46), (17, 99, 41), (154, 103, 0), (9, 105, 218), (130, 80, 223), (27, 124, 131), (110, 119, 129)],
    [(87, 96, 106), (164, 14, 38), (26, 127, 55), (125, 78, 0), (33, 139, 255), (161, 92, 236), (49, 146, 170), (140, 149, 159)])


def tmux(*args: str) -> str:
    return subprocess.run(["tmux", "-L", SOCKET, *args], capture_output=True, text=True, check=True).stdout


def prepare_claude(paths) -> None:
    """Claude Code's config for the made-up home: onboarding done, light theme, folders trusted,
    and the user's own provider settings so it starts without asking to log in."""
    projects = {str(demo.HOME / "code" / p): {"hasTrustDialogAccepted": True, "hasCompletedProjectOnboarding": True,
                                              "projectOnboardingSeenCount": 5}
                for p, *_ in demo.SESSIONS}
    (paths.claude / ".claude.json").write_text(json.dumps({
        "hasCompletedOnboarding": True, "theme": "light", "numStartups": 20, "autoUpdates": False,
        "hasSeenTasksHint": True, "projects": projects}))
    try:
        env = json.loads((REAL_HOME / ".claude" / "settings.json").read_text()).get("env") or {}
    except (OSError, ValueError):
        env = {}
    (paths.claude / "settings.json").write_text(json.dumps({"env": env, "theme": "light", "spinnerTipsEnabled": False}))
    # No AWS credentials on purpose: drawing a resumed conversation needs none, and without them
    # nothing here can call a model.


def stage(paths) -> None:
    # The featured session's made-up "running elsewhere" status would make csm ask before resuming
    # it; once it's open, Claude Code reports its own.
    featured = next(s.id for s in demo.data.load_sessions(paths) if s.title.startswith("Fix the checkout"))
    for f in paths.live.glob("*.json"):
        if json.loads(f.read_text()).get("sessionId") == featured:
            f.unlink()
    # csm, started for real, looks for its caches in the usual place under HOME.
    cache = demo.HOME / ".cache" / "csm"
    cache.mkdir(parents=True, exist_ok=True)
    for f in (paths.prs, paths.summaries):
        shutil.copy(f, cache / f.name)
    env = {"HOME": str(demo.HOME), "CLAUDE_CONFIG_DIR": str(paths.claude), "CSM_THEME": "ansi-light",
           "CSM_STUCK_MINUTES": "0", "XDG_STATE_HOME": str(demo.HOME / ".local" / "state"),
           "PATH": os.environ["PATH"], "TERM": "xterm-256color", "COLORTERM": "truecolor"}
    sidebar = f"{sys.executable} -m csm --sidebar --no-notify"
    tmux("-f", str(CONF), "new-session", "-d", "-s", "csm", "-x", str(WIDTH), "-y", str(HEIGHT),
         *[x for k, v in env.items() for x in ("-e", f"{k}={v}")], "-c", str(demo.HOME), sidebar)
    time.sleep(6)
    side = tmux("list-panes", "-F", "#{pane_id}").split()[0]
    # Open the featured session the way you would: filter to it, then enter.
    tmux("send-keys", "-t", side, "/")
    tmux("send-keys", "-t", side, "-l", "checkout")
    tmux("send-keys", "-t", side, "Enter")
    time.sleep(0.5)
    tmux("send-keys", "-t", side, "Enter")
    time.sleep(1.5)
    tmux("select-pane", "-t", side)
    tmux("send-keys", "-t", side, "Escape")  # clear the filter, so the list shows everything again
    time.sleep(1.0)
    panes = tmux("list-panes", "-F", "#{pane_id}").split()
    if len(panes) > 1:
        tmux("select-pane", "-t", panes[1])  # focus on the session, as when you're working in it
        # With no terminal attached tmux sends no focus events, so tell csm itself it lost focus
        # (ESC [ O), as it would hear when you click into the session.
        tmux("send-keys", "-t", side, "-H", "1b", "5b", "4f")
    time.sleep(8)  # Claude Code starting and drawing the conversation


def capture() -> Text:
    rows = [line.split("\t") for line in tmux("list-panes", "-F", "#{pane_id}\t#{pane_width}\t#{pane_active}").splitlines()]
    shots = []
    for pid, width, active in rows:
        lines = tmux("capture-pane", "-p", "-e", "-t", pid).split("\n")[:HEIGHT]
        lines += [""] * (HEIGHT - len(lines))
        shots.append((int(width), active == "1", lines))
    out = Text()
    for y in range(HEIGHT):
        for n, (width, active, lines) in enumerate(shots):
            if n:  # tmux's divider, in the active pane's color
                out.append("┃", style="#ff8700" if active else "#d0d7de")
            cell = Text.from_ansi(lines[y], end="")
            for word in SCRUB:
                if (at := cell.plain.find(word)) != -1:
                    cell = cell[:at] + cell[at + len(word):]
            cell.truncate(width, pad=True)
            out.append(cell)
        out.append("\n")
    out.rstrip()
    return out


def main() -> None:
    os.environ["XDG_STATE_HOME"] = str(demo.HOME / ".local" / "state")
    try:
        paths = demo.build(demo.HOME)
        prepare_claude(paths)
        stage(paths)
        shot = capture()
        console = Console(width=WIDTH + 1, record=True, file=io.StringIO(), color_system="truecolor")
        console.print(shot, crop=True, overflow="crop", no_wrap=True)
        demo.OUT.mkdir(parents=True, exist_ok=True)
        (demo.OUT / "sidebar.svg").write_text(console.export_svg(title="csm", theme=LIGHT))
        print((demo.OUT / "sidebar.svg").relative_to(demo.ROOT))
    finally:
        subprocess.run(["tmux", "-L", SOCKET, "kill-server"], capture_output=True)
        shutil.rmtree(demo.HOME, ignore_errors=True)


if __name__ == "__main__":
    main()
