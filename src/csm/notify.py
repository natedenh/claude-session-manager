"""Desktop notifications via OSC 9, which Ghostty turns into a macOS notification."""
from __future__ import annotations

import os
import subprocess


def send(message: str) -> None:
    """Best effort: a missing tty or tmux must never bother the list."""
    message = "".join(c for c in message if c.isprintable())
    seq = f"\033]9;{message}\a"
    try:
        tty = "/dev/tty"
        if os.environ.get("TMUX"):
            # Written straight to the client's tty, so it needs no passthrough wrapping.
            tty = subprocess.run(["tmux", "display-message", "-p", "#{client_tty}"],
                                 capture_output=True, text=True, check=True, timeout=2).stdout.strip() or tty
        with open(tty, "w") as f:
            f.write(seq)
    except (OSError, subprocess.SubprocessError):
        pass
