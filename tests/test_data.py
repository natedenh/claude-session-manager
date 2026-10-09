import json
import os
import time

from conftest import assistant, rec, user

from csm import data


def test_title_precedence_and_last_rename_wins(paths, write, tmp_path):
    repo = str(tmp_path / "repo")
    write(repo, "a", user("<command-name>/clear</command-name>", repo), user("first   prompt\nhere", repo))
    write(repo, "b", user("prompt", repo), rec(type="ai-title", aiTitle="AI title"))
    write(repo, "c", user("prompt", repo), rec(type="ai-title", aiTitle="AI title"),
          rec(type="custom-title", customTitle="Old"), rec(type="custom-title", customTitle="New"))
    write(repo, "empty", rec(type="queue-operation"))
    titles = {s.id: s.title for s in data.load_sessions(paths)}
    assert titles == {"a": "first prompt here", "b": "AI title", "c": "New"}


def test_wandering_cwd_does_not_regroup(paths, write, tmp_path):
    repo = str(tmp_path / "repo")
    sub = tmp_path / "repo" / "app" / "src"
    sub.mkdir(parents=True)
    write(repo, "a", user("hi", repo), user("later", str(sub)))
    [s] = data.load_sessions(paths)
    assert s.project == repo and s.cwd == repo and not s.worktree


def test_worktree_sessions_fold_into_repo(paths, write, tmp_path):
    repo = str(tmp_path / "repo")
    wt = f"{repo}/.claude/worktrees/feature"
    os.makedirs(wt)
    write(repo, "entered", user("hi", repo), user("now in worktree", wt))
    write(wt, "launched", user("hi", wt))
    by_id = {s.id: s for s in data.load_sessions(paths)}
    assert by_id["entered"].project == repo and by_id["entered"].worktree
    assert by_id["entered"].cwd == repo  # where `claude -r` can find it
    assert by_id["launched"].project == repo and by_id["launched"].worktree
    assert by_id["launched"].cwd == wt


def test_relocated_copies_are_deduped(paths, write, tmp_path):
    repo = str(tmp_path / "repo")
    wt = f"{repo}/.claude/worktrees/feature"
    os.makedirs(wt)
    old = write(repo, "same", user("hi", repo))
    os.utime(old, (time.time() - 100,) * 2)
    write(wt, "same", user("hi", repo), rec(type="relocated", relocatedCwd=wt))
    [s] = data.load_sessions(paths)
    assert s.cwd == wt and s.worktree


def test_pr_and_cost(paths, write, tmp_path):
    repo = str(tmp_path / "repo")
    write(repo, "a", user("hi", repo, gitBranch="feat/x"),
          rec(type="pr-link", prNumber=12, prUrl="https://github.com/o/r/pull/12"),
          rec(type="cost-state", totalCostUSD=1.5))
    [s] = data.load_sessions(paths)
    assert (s.pr_number, s.pr_url, s.cost, s.branch) == (12, "https://github.com/o/r/pull/12", 1.5, "feat/x")


def test_cache_is_reused_until_file_changes(paths, write, tmp_path, monkeypatch):
    repo = str(tmp_path / "repo")
    f = write(repo, "a", user("one", repo))
    data.load_sessions(paths)
    calls = []
    real = data.parse_session
    monkeypatch.setattr(data, "parse_session", lambda p: calls.append(p) or real(p))
    assert data.load_sessions(paths)[0].title == "one"
    assert calls == []
    with open(f, "a") as fh:
        fh.write(rec(type="custom-title", customTitle="two") + "\n")
    assert data.load_sessions(paths)[0].title == "two"
    assert len(calls) == 1


def test_live_ignores_dead_pids(paths):
    (paths.live / "1.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "a", "status": "busy", "entrypoint": "cli"}))
    (paths.live / "2.json").write_text(json.dumps({"pid": 999_999_999, "sessionId": "b", "status": "idle"}))
    (paths.live / "3.json").write_text("not json")
    live = data.load_live(paths)
    assert list(live) == ["a"] and live["a"].status == "busy"


def test_transcript_merges_and_counts_tools(paths, write, tmp_path):
    repo = str(tmp_path / "repo")
    tool = {"type": "tool_use", "name": "Bash", "input": {}}
    f = write(repo, "a",
              user("question", repo),
              user("<system-reminder>x</system-reminder>", repo),
              assistant("looking", repo),
              assistant([tool], repo),
              user([{"type": "tool_result", "content": "out"}], repo),
              assistant([tool, tool], repo),
              assistant("answer", repo),
              assistant("more", repo),
              rec(type="assistant", isSidechain=True, message={"content": "subagent"}))
    msgs = [(m.role, m.text) for m in data.transcript(f)]
    assert msgs == [("user", "question"), ("assistant", "looking"), ("tools", "3"),
                    ("assistant", "answer\n\nmore")]


def test_transcript_tail_skips_partial_line(paths, write, tmp_path):
    repo = str(tmp_path / "repo")
    last = user("last", repo)
    f = write(repo, "a", user("x" * 500, repo), last)
    assert [m.text for m in data.transcript(f, tail_bytes=len(last) + 50)] == ["last"]


def test_search_finds_message_text_only(paths, write, tmp_path):
    repo = str(tmp_path / "repo")
    write(repo, "hit", user("hi", repo), assistant("The Cognito pool is shared", repo))
    write(repo, "meta", user("hi", repo), rec(type="attachment", stdout="cognito"))
    write(repo, "miss", user("hi", repo))
    sessions = data.load_sessions(paths)
    hits = data.search(sessions, "cognito")
    assert list(hits) == ["hit"] and "Cognito pool" in hits["hit"][0]
    assert data.search(sessions, "cognito", cancelled=lambda: True) == {}


def test_rename_appends_record(paths, write, tmp_path):
    repo = str(tmp_path / "repo")
    f = write(repo, "a", user("hi", repo))
    f.write_text(f.read_text().rstrip("\n"))  # no trailing newline
    [s] = data.load_sessions(paths)
    data.rename(s, "Renamed")
    assert data.load_sessions(paths)[0].title == "Renamed"
    assert all(json.loads(line) for line in f.read_text().splitlines())


def test_trash_moves_transcript_and_subagents(paths, write, tmp_path):
    repo = str(tmp_path / "repo")
    f = write(repo, "a", user("hi", repo))
    (f.parent / "a" / "subagents").mkdir(parents=True)
    [s] = data.load_sessions(paths)
    dest = data.trash(s, paths)
    assert dest.exists() and not f.exists() and not (f.parent / "a").exists()
    assert data.load_sessions(paths) == []


def test_state_roundtrip(paths):
    st = data.State(paths.state)
    st.archived.add("a")
    st.collapsed.add("/repo")
    st.save()
    st2 = data.State(paths.state)
    assert st2.archived == {"a"} and st2.collapsed == {"/repo"}


def test_load_desktop_maps_cli_ids(paths):
    d = paths.desktop / "Claude-3p" / "claude-code-sessions" / "acct" / "org"
    d.mkdir(parents=True)
    (d / "local_a.json").write_text(json.dumps({"sessionId": "local_a", "cliSessionId": "x", "isArchived": True}))
    (d / "local_b.json").write_text(json.dumps({"sessionId": "local_b", "cliSessionId": "y"}))
    (d / "local_c.json").write_text("{broken")
    recs = data.load_desktop(paths)
    assert recs == {"x": data.DesktopRecord("local_a", True), "y": data.DesktopRecord("local_b", False)}
    (d / "local_a.json").write_text(json.dumps({"sessionId": "local_a", "cliSessionId": "x", "isArchived": False}))
    os.utime(d / "local_a.json", (time.time() + 5,) * 2)
    assert not data.load_desktop(paths)["x"].archived


def _fork_lines(cwd, sid_in_records):
    return [rec(type="user", uuid="u1", sessionId=sid_in_records, cwd=cwd, message={"content": "hi"}),
            rec(type="custom-title", customTitle="Same title")]


def test_forks_link_to_the_session_named_in_copied_records(paths, write, tmp_path):
    repo = str(tmp_path / "repo")
    write(repo, "orig", *_fork_lines(repo, "orig"))
    write(repo, "fork", *_fork_lines(repo, "orig"))  # copied records still carry the original's id
    by = {s.id: s for s in data.load_sessions(paths)}
    assert by["fork"].forked_from == "orig" and by["orig"].forked_from is None


def test_forks_with_rewritten_ids_link_to_the_older_file(paths, write, tmp_path, monkeypatch):
    repo = str(tmp_path / "repo")
    write(repo, "old", *_fork_lines(repo, "old"))
    write(repo, "new", *_fork_lines(repo, "new"))
    real = data.parse_session

    def parse(path):
        s = real(path)
        s.born = {"old": 1.0, "new": 2.0}[s.id]
        return s
    monkeypatch.setattr(data, "parse_session", parse)
    by = {s.id: s for s in data.load_sessions(paths)}
    assert by["new"].forked_from == "old" and by["old"].forked_from is None


def test_unrelated_sessions_are_not_forks(paths, write, tmp_path):
    repo = str(tmp_path / "repo")
    write(repo, "a", rec(type="user", uuid="u1", sessionId="a", cwd=repo, message={"content": "one"}))
    write(repo, "b", rec(type="user", uuid="u2", sessionId="b", cwd=repo, message={"content": "two"}))
    assert all(s.forked_from is None for s in data.load_sessions(paths))


def test_state_save_keeps_changes_made_elsewhere(tmp_path):
    f = tmp_path / "state.json"
    mine = data.State(f)
    mine.archived.add("a")
    mine.save()
    other = data.State(f)
    other.archived |= {"b"}
    other.set_note("a", "from elsewhere")
    other.save()
    mine.archived.discard("a")  # this csm unarchives a, never having seen b or the note
    mine.pinned.add("c")
    mine.save()
    d = data.State(f)
    assert d.archived == {"b"} and d.pinned == {"c"} and d.notes == {"a": "from elsewhere"}


def test_state_reload_picks_up_outside_edits_and_keeps_unsaved(tmp_path):
    f = tmp_path / "state.json"
    st = data.State(f)
    assert not st.reload()
    st.pinned.add("p")  # not saved yet
    f.write_text(json.dumps({"archived": ["x"], "flat": True}))
    assert st.reload()
    assert st.archived == {"x"} and st.flat and st.pinned == {"p"}
    assert not st.reload()


def test_viewers_map_background_jobs_to_attached_terminals():
    live = {"t": data.LiveSession(1, "t", "idle", "cli", kind="interactive", parked_job_id="j1"),
            "b": data.LiveSession(2, "b", "busy", "cli", kind="bg", job_id="j1"),
            "lone": data.LiveSession(3, "lone", "idle", "cli", kind="bg", job_id="j2"),
            "gone": data.LiveSession(4, "gone", "idle", "cli", parked_job_id="j9")}
    assert data.viewers(live) == {"b": "t"}


def test_history_by_local_day_hour_cost_lines_and_skills(paths, write, monkeypatch, tmp_path):
    monkeypatch.setenv("TZ", "America/Chicago")
    time.tzset()
    try:
        def at(ts, **kw):
            return rec(timestamp=ts, **kw)
        cwd = str(tmp_path)
        f = write(cwd, "h1",
                  user("hi", cwd),  # 2026-09-30T16:05Z = 11:05 local
                  at("2026-09-30T16:05:50Z", type="assistant", message={"content": [
                      {"type": "tool_use", "name": "Skill", "input": {"skill": "browser-test"}}]}),
                  rec(type="cost-state", totalCostUSD=1.5, totalLinesAdded=10),
                  at("2026-09-30T16:07:00Z", type="assistant", message={"content": "ok"}),
                  at("2026-09-30T16:08:00Z", type="assistant", isSidechain=True, message={"content": "sub"}),
                  at("2026-10-01T04:30:00Z", type="user", message={"content": "late"}),  # 23:30 on the 30th locally
                  at("2026-10-01T15:00:00Z", type="user", message={"content": "next day"}),
                  rec(type="cost-state", totalCostUSD=1.0, totalLinesAdded=4),  # a new process: a drop adds nothing
                  rec(type="cost-state", totalCostUSD=1.25, totalLinesAdded=6),
                  at("2026-10-01T15:00:30Z", type="assistant", message={"content": [
                      {"type": "tool_use", "name": "Skill", "input": {"skill": "browser-test"}}]}))
        s = data.parse_session(f)
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()
    # 11:05, 11:07 and 23:30 on the 30th (the sidechain minute doesn't count); 10:00 on the 1st
    assert s.active == {"2026-09-30": 3, "2026-10-01": 1}
    assert s.hours[11] == 2 and s.hours[23] == 1 and s.hours[10] == 1 and sum(s.hours) == 4
    assert s.day_cost == {"2026-09-30": 1.5, "2026-10-01": 0.25}
    assert s.day_lines == {"2026-09-30": 10, "2026-10-01": 2}
    assert s.skills == {"browser-test": 2}


def test_recorded_cost_is_spread_over_the_messages_it_paid_for(paths, write, tmp_path):
    cwd = str(tmp_path)

    def msg(mid, ts, out):  # two lines for one message, as Claude Code writes a block per line
        line = rec(type="assistant", timestamp=ts, message={"id": mid, "content": [],
                   "usage": {"input_tokens": 0, "cache_creation_input_tokens": 0,
                             "cache_read_input_tokens": 0, "output_tokens": out}})
        return [line, line]
    f = write(cwd, "c1", user("hi", cwd),
              *msg("m1", "2026-09-29T15:00:00Z", 100),  # output counts 5x: 500 weight on the 29th
              *msg("m2", "2026-09-30T15:00:00Z", 300),  # 1500 on the 30th
              rec(type="cost-state", totalCostUSD=4.0),
              *msg("m3", "2026-10-01T15:00:00Z", 200))  # after the last record: not priced yet
    s = data.parse_session(f)
    assert s.day_cost == {"2026-09-29": 1.0, "2026-09-30": 3.0}  # 500 : 1500 weight
    assert s.priced_weight == 2000.0 and s.day_unpriced == {"2026-10-01": 1000.0}
    from csm import stats
    assert stats.rate([s]) == 4.0 / 2000 and stats.spend(s, "2026-10-01", stats.rate([s])) == 2.0


def test_concurrent_json_writes_dont_collide(tmp_path):
    import threading
    target = tmp_path / "prs.json"
    errors = []

    def writer(n):
        try:
            for i in range(200):
                data._write_json(target, {"writer": n, "i": i})
        except Exception as e:  # the old fixed prs.tmp name raised FileNotFoundError here
            errors.append(e)
    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and json.loads(target.read_text())["i"] == 199
    assert [p.name for p in tmp_path.iterdir()] == ["prs.json"]  # no temp files left behind


def test_models_per_day_come_from_the_message_not_tool_inputs(paths, write, tmp_path):
    cwd = str(tmp_path)

    def msg(mid, model, out, tool_model=None):
        content = [{"type": "tool_use", "name": "Agent", "input": {"model": tool_model}}] if tool_model else []
        return rec(type="assistant", timestamp="2026-09-30T15:00:00Z",
                   message={"model": model, "id": mid, "content": content,
                            "usage": {"input_tokens": 0, "output_tokens": out}})
    f = write(cwd, "m1", user("hi", cwd), msg("a", "claude-opus-5-5", 100, tool_model="sonnet"),
              msg("a", "claude-opus-5-5", 100), msg("b", "claude-sonnet-5-5", 40), msg("c", "<synthetic>", 9))
    s = data.parse_session(f)
    day = next(iter(s.day_models))
    assert s.day_models[day] == {"claude-opus-5-5": 500.0, "claude-sonnet-5-5": 200.0}
