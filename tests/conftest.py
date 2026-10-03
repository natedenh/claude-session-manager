import json
import os

import pytest

from csm import data


def rec(**kw) -> str:
    return json.dumps(kw, separators=(",", ":"))


def user(text, cwd, **kw):
    return rec(type="user", cwd=cwd, timestamp="2026-09-30T16:05:31.417Z",
               message={"role": "user", "content": text}, **kw)


def assistant(content, cwd):
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    return rec(type="assistant", cwd=cwd, message={"role": "assistant", "content": content})


@pytest.fixture
def paths(tmp_path):
    p = data.Paths(claude=tmp_path / "claude", cache=tmp_path / "cache.json",
                   state=tmp_path / "state.json", trash=tmp_path / "Trash", desktop=tmp_path / "desktop")
    p.projects.mkdir(parents=True)
    p.live.mkdir()
    return p


@pytest.fixture
def write(paths):
    """write(launch_dir, session_id, *lines) -> transcript path."""
    def _write(launch_dir, sid, *lines):
        os.makedirs(launch_dir, exist_ok=True)
        d = paths.projects / data.encode_dir(str(launch_dir))
        d.mkdir(exist_ok=True)
        f = d / f"{sid}.jsonl"
        f.write_text("".join(line + "\n" for line in lines))
        return f
    return _write


@pytest.fixture(autouse=True)
def no_real_ghostty(monkeypatch):
    """Never script the user's actual Ghostty from tests."""
    from csm import ghostty
    monkeypatch.setattr(ghostty, "running", lambda: False)
