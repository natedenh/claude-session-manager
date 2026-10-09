"""Everything csm launches outside the terminal: browser, editor, Finder, desktop app."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys

OPENER = "open" if sys.platform == "darwin" else "xdg-open"  # opens a file, folder or URL with its app


def run(argv: list[str]) -> None:
    """The one place external programs are started, detached. Tests replace it."""
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def open_url(url: str) -> None:
    run([OPENER, url])


def editor_argv(path: str) -> list[str]:
    """$CSM_EDITOR, else code, else cursor, else Finder."""
    if custom := os.environ.get("CSM_EDITOR", "").strip():
        return [*shlex.split(custom), path]
    for name in ("code", "cursor"):
        if shutil.which(name):
            return [name, path]
    return [OPENER, path]


def open_editor(path: str) -> None:
    run(editor_argv(path))
