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
    p = data.Paths(claude=tmp_path / "claude", cache=tmp_path / "cache.json", prs=tmp_path / "prs.json",
                   state=tmp_path / "state.json", status=tmp_path / "status", trash=tmp_path / "Trash", export=tmp_path / "export", desktop=tmp_path / "desktop", summaries=tmp_path / "summaries.json")
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


@pytest.fixture(autouse=True)
def no_real_gh(monkeypatch):
    """Never shell out to the real gh from tests."""
    from csm import prs
    monkeypatch.setattr(prs, "fetch", lambda url: None)


@pytest.fixture(autouse=True)
def fixed_theme(monkeypatch):
    """Skip the macOS appearance lookup (tests stub out subprocess.run)."""
    monkeypatch.setenv("CSM_THEME", "ansi-dark")


@pytest.fixture(autouse=True)
def sent(monkeypatch):
    """Never write escape sequences to a real tty; collect what would have been sent."""
    from csm import notify
    out = []
    monkeypatch.setattr(notify, "send", out.append)
    return out


@pytest.fixture(autouse=True)
def no_real_finder(monkeypatch):
    """Never open Finder from tests; revealed files are recorded in `revealed`."""
    from csm import export
    revealed = []
    monkeypatch.setattr(export, "reveal", revealed.append)
    return revealed


@pytest.fixture(autouse=True)
def launched(monkeypatch):
    """Never open a browser, editor, Finder or the desktop app; commands are recorded here."""
    from csm import links
    out = []
    monkeypatch.setattr(links, "run", out.append)
    return out


@pytest.fixture(autouse=True)
def no_real_model(monkeypatch):
    """Never call Claude from tests; summary tests pass their own fake client."""
    from csm import summary

    def refuse():
        raise RuntimeError("no model in tests")
    monkeypatch.setattr(summary, "make_client", refuse)
