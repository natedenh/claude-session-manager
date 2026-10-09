"""Which sessions were open in csm's panes, so a reboot can put them back.

Saved whenever the panes change: the sessions running on csm's tmux server, and which of
them were beside the list, top to bottom.
"""
from __future__ import annotations

import json
from pathlib import Path

from .data import _write_json


def load(path: Path) -> dict:
    try:
        d = json.loads(path.read_text())
        hosted, shown = d.get("hosted"), d.get("shown")
        if isinstance(hosted, list) and isinstance(shown, list):
            return {"hosted": [x for x in hosted if isinstance(x, str)], "shown": [x for x in shown if isinstance(x, str)]}
    except (OSError, ValueError, AttributeError):
        pass
    return {"hosted": [], "shown": []}


def save(path: Path, hosted: list[str], shown: list[str]) -> None:
    try:
        _write_json(path, {"hosted": hosted, "shown": shown})
    except OSError:
        pass  # only a convenience: losing it means a reboot doesn't reopen anything
