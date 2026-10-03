"""Runs against a real, throwaway tmux server."""
import os
import shutil
import subprocess

import pytest

from csm.tmux import CONF, Tmux

pytestmark = pytest.mark.skipif(not shutil.which("tmux"), reason="tmux not installed")


@pytest.fixture
def server():
    base = ["tmux", "-L", f"csm-test-{os.getpid()}", "-f", str(CONF)]
    subprocess.run([*base, "new-session", "-d", "-s", "csm", "-x", "200", "-y", "50", "sleep 600"], check=True)
    me = subprocess.run([*base, "list-panes", "-F", "#{pane_id}"], capture_output=True, text=True).stdout.strip()
    t = Tmux(base, me=me)
    t.mark_sidebar()
    yield t
    subprocess.run([*base, "kill-server"])


def beside(t):
    mine = next(p for p in t.panes() if p.id == t.me)
    return [p.session_id for p in t.panes() if p.window == mine.window and p.id != t.me]


def test_config_binds_switch_key_and_drops_prefix(server):
    assert server.run("show", "-gv", "prefix") == "None"
    assert "select-pane -R" in server.run("list-keys", "-T", "root")


def test_show_swaps_sessions_and_keeps_them_running(server):
    server.show("a", "/tmp", "sleep 600")
    assert beside(server) == ["a"]
    server.show("b", "/tmp", "sleep 600")
    assert beside(server) == ["b"] and set(server.hosted()) == {"a", "b"}
    server.show("a", "/tmp", "sleep 600")
    assert beside(server) == ["a"] and server.shown() == "a"


def test_show_keeps_sidebar_width(server):
    server.show("a", "/tmp", "sleep 600")
    server.run("resize-pane", "-t", server.me, "-x", "50")
    server.show("b", "/tmp", "sleep 600")
    assert next(p for p in server.panes() if p.id == server.me).width == 50


def test_close(server):
    server.show("a", "/tmp", "sleep 600")
    assert server.close("a") and server.hosted() == {} and beside(server) == []
    assert not server.close("a")
