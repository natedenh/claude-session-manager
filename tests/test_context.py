from conftest import rec, user

from csm import data
from csm.app import CSM
from csm.fmt import context_flag, context_line, context_style


def turn(tokens, model="claude-opus-5", sidechain=False, inp=1, out=4):
    return rec(type="assistant", isSidechain=sidechain,
               message={"model": model, "role": "assistant", "content": [],
                        "usage": {"input_tokens": inp, "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": tokens - inp - out, "output_tokens": out}})


def parsed(paths, write, tmp_path, *lines):
    write(str(tmp_path / "p"), "a", user("hi", str(tmp_path / "p")), *lines)
    [s] = data.load_sessions(paths)
    return s


def test_last_main_thread_usage_wins_and_sidechains_are_ignored(paths, write, tmp_path):
    synthetic = rec(type="assistant", message={"model": "<synthetic>", "usage": {"input_tokens": 0}})
    s = parsed(paths, write, tmp_path, turn(1000), turn(50_000, model="claude-sonnet-5-5"),
               turn(190_000, sidechain=True), synthetic)
    assert (s.context_tokens, s.context_model) == (50_000, "claude-sonnet-5-5")


def test_no_usage_means_no_context(paths, write, tmp_path):
    s = parsed(paths, write, tmp_path)
    assert s.context_tokens is None and context_line(s) is None


def test_window_size():
    assert data.context_window("claude-opus-5") == 200_000
    assert data.context_window("claude-opus-5[1m]") == 1_000_000
    assert data.context_window("claude-opus-5", 300_000) == 1_000_000  # transcripts drop the suffix
    assert data.context_window(None) == 200_000


def test_style_thresholds():
    assert [context_style(f) for f in (0.49, 0.5, 0.8, 0.81)] == ["dim", "warn", "warn", "red"]


def test_preview_text(paths, write, tmp_path):
    s = parsed(paths, write, tmp_path, turn(104_000))
    line = context_line(s)
    assert line.plain == "context ▰▰▰▰▰▱▱▱▱▱ 52% · 104k of 200k tokens"
    assert line.style == "warn"


def test_row_flag_only_above_80_percent(paths, write, tmp_path):
    s = parsed(paths, write, tmp_path, turn(160_000))
    assert context_flag(s).plain == ""
    s.context_tokens = 160_001
    assert context_flag(s).plain == " ◔"
    assert "◔" in CSM(paths).row(s).plain


def test_cache_version_bumped():
    assert data.CACHE_VERSION >= 5
