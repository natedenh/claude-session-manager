"""Runs against a real, throwaway tmux server."""
import os
import shutil
import subprocess
import time

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


def test_respawned_sidebar_keeps_sessions_and_mark(server):
    server.show("a", "/tmp", "sleep 600")
    old_pid = server.run("display", "-p", "-t", server.me, "#{pane_pid}")
    server.run("respawn-pane", "-k", "-t", server.me, "sleep 600")
    assert server.run("display", "-p", "-t", server.me, "#{pane_pid}") != old_pid
    assert server.hosted().keys() == {"a"} and beside(server) == ["a"]
    assert next(p for p in server.panes() if p.id == server.me).sidebar


def test_send_types_literal_text(server):
    server.show("a", "/tmp", "cat")
    text = "-n hello $HOME; x"
    assert server.send("a", text)
    for _ in range(30):
        out = server.run("capture-pane", "-p", "-t", server.hosted()["a"])
        if out.count(text) == 2:  # the tty echo, then cat's copy
            break
        time.sleep(0.1)
    assert out.count(text) == 2
    assert not server.send("missing", "x")


def test_show_also_splits_and_show_collapses(server):
    server.show("a", "/tmp", "sleep 600")
    server.show_also("b", "/tmp", "sleep 600")
    assert server.shown_all() == ["a", "b"] and server.shown() == "a"
    assert beside(server) == ["a", "b"]
    server.show_also("b", "/tmp", "sleep 600")  # already visible
    assert server.shown_all() == ["a", "b"]
    server.show("b", "/tmp", "sleep 600")
    assert server.shown_all() == ["b"] and set(server.hosted()) == {"a", "b"}
    server.show("a", "/tmp", "sleep 600")
    assert server.shown_all() == ["a"]


def test_show_also_with_nothing_shown_is_plain_show(server):
    server.show_also("a", "/tmp", "sleep 600")
    assert server.shown_all() == ["a"]
