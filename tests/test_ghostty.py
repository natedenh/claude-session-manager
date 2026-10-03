from csm.data import LiveSession, Session
from csm.ghostty import Terminal, find


def sess(title="Fix login bug", cwd="/p/repo"):
    return Session(id="a", path="x", title=title, project="/p/repo", cwd=cwd)


def live(**kw):
    return LiveSession(pid=1, session_id="a", status="idle", entrypoint="cli", **kw)


def test_matches_title_behind_spinner_glyph():
    terms = [Terminal("1", "/p/repo", "~/p/repo"), Terminal("2", "/p/repo", "✳ Fix login bug")]
    assert find(sess(), live(), terms).id == "2"


def test_matches_truncated_title():
    terms = [Terminal("1", "/p/repo", "◐ Fix login…")]
    assert find(sess(), live(), terms).id == "1"


def test_same_title_disambiguated_by_directory():
    terms = [Terminal("1", "/elsewhere", "✳ Fix login bug"), Terminal("2", "/p/repo/src", "✳ Fix login bug")]
    assert find(sess(), live(), terms).id == "2"


def test_falls_back_to_lone_claude_terminal_in_directory():
    terms = [Terminal("1", "/p/repo", "✳ Claude Code"), Terminal("2", "/p/repo", "~/p/repo")]
    assert find(sess(), live(cwd="/p/repo"), terms).id == "1"


def test_no_match():
    terms = [Terminal("1", "/p/repo", "✳ Claude Code"), Terminal("2", "/p/repo", "✳ Other thing")]
    assert find(sess(), live(cwd="/p/repo"), terms) is None
