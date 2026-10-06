"""Precise session status from Claude Code hooks.

`csm hook` runs inside every Claude session and records its state in a file per
session; `csm hooks install` wires it into settings.json. The app only reads the files.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from datetime import datetime
from pathlib import Path

from . import data

EVENTS = ("Notification", "Stop", "UserPromptSubmit", "SessionEnd")
MARK = "csm hook"  # how we recognize our own entries
DEFAULT_SETTINGS = data.HOME / ".claude" / "settings.json"


def state_for(payload: dict) -> str | None:
    event = payload.get("hook_event_name")
    if event == "Notification":
        return "permission" if "permission" in str(payload.get("message", "")).lower() else "waiting"
    return {"Stop": "waiting", "UserPromptSubmit": "working", "SessionEnd": "ended"}.get(event)


def record(payload: dict, status_dir: Path) -> None:
    sid = payload.get("session_id")
    state = state_for(payload)
    if not isinstance(sid, str) or not sid or "/" in sid or state is None:
        return
    target = status_dir / f"{sid}.json"
    if state == "ended":
        target.unlink(missing_ok=True)
        return
    status_dir.mkdir(parents=True, exist_ok=True)
    tmp = status_dir / f".{sid}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps({"event": payload["hook_event_name"], "state": state,
                               "message": payload.get("message"), "at": time.time()}))
    os.replace(tmp, target)


def run_hook() -> int:
    try:
        record(json.load(sys.stdin), data.Paths().status)
    except Exception:
        pass
    return 0


def load_status(status_dir: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    try:
        files = list(status_dir.glob("*.json"))
    except OSError:
        return out
    for f in files:
        try:
            d = json.loads(f.read_text())
            if d["state"] in ("permission", "waiting", "working") and isinstance(d["at"], (int, float)):
                out[f.stem] = d
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out


# ---- settings.json ----------------------------------------------------------

def hook_command() -> str:
    return f"{shlex.quote(sys.executable)} -m csm hook"


def ours(group: object) -> list[dict]:
    hooks = group.get("hooks") if isinstance(group, dict) else None
    return [h for h in hooks or [] if isinstance(h, dict) and MARK in str(h.get("command", ""))]


def read_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    cfg = json.loads(path.read_text() or "{}")
    if not isinstance(cfg, dict):
        raise ValueError("not a JSON object")
    return cfg


def plan(cfg: dict, install: bool) -> list[str]:
    """Edit cfg in place; return a description of each change."""
    changes: list[str] = []
    hooks = cfg.get("hooks")
    if hooks is not None and not isinstance(hooks, dict):
        raise ValueError("\"hooks\" is not an object")
    if install:
        hooks = cfg.setdefault("hooks", {})
        for ev in EVENTS:
            groups = hooks.setdefault(ev, [])
            if not any(ours(g) for g in groups):
                groups.append({"hooks": [{"type": "command", "command": hook_command()}]})
                changes.append(f"added {ev}: {hook_command()}")
        return changes
    for ev in EVENTS:
        groups = (hooks or {}).get(ev)
        if not isinstance(groups, list) or not any(ours(g) for g in groups):
            continue
        for g in groups:
            if mine := ours(g):
                g["hooks"] = [h for h in g["hooks"] if h not in mine]
                changes += [f"removed {ev}: {h['command']}" for h in mine]
        hooks[ev] = [g for g in groups if not (isinstance(g, dict) and g.get("hooks") == [])]
        if not hooks[ev]:
            del hooks[ev]
    if hooks == {} and changes:
        del cfg["hooks"]
    return changes


def status_report(cfg: dict) -> list[str]:
    hooks = cfg.get("hooks") if isinstance(cfg.get("hooks"), dict) else {}
    return [f"{ev}: {'installed' if any(ours(g) for g in hooks.get(ev) or []) else 'not installed'}"
            for ev in EVENTS]


def hooks_main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="csm hooks", description="Manage csm's Claude Code hooks.")
    ap.add_argument("action", choices=["install", "uninstall", "status"])
    ap.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS, help="settings file; default ~/.claude/settings.json")
    ap.add_argument("--yes", "-y", action="store_true", help="don't ask for confirmation when installing")
    args = ap.parse_args(argv)
    path: Path = args.settings.expanduser()
    try:
        cfg = read_settings(path)
        if args.action == "status":
            print(f"{path}:")
            print("\n".join("  " + line for line in status_report(cfg)))
            return 0
        changes = plan(cfg, args.action == "install")
    except (OSError, ValueError) as e:
        print(f"csm: can't use {path}: {e}", file=sys.stderr)
        return 1
    if not changes:
        print(f"{path}: nothing to change")
        return 0
    print(f"{path}:")
    print("\n".join("  " + c for c in changes))
    if args.action == "install" and not args.yes and input("Apply? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Aborted.")
        return 1
    if path.exists():
        backup = path.with_name(f"{path.name}.bak-csm-{datetime.now():%Y%m%d-%H%M%S}")
        backup.write_bytes(path.read_bytes())
        print(f"backup: {backup}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.csm-tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n")
    os.replace(tmp, path)
    return 0


def dispatch(argv: list[str]) -> int | None:
    """Handle `hook` / `hooks …` before the heavy imports; None means not ours."""
    if argv[:1] == ["hook"]:
        return run_hook()
    if argv[:1] == ["hooks"]:
        return hooks_main(argv[1:])
    return None
