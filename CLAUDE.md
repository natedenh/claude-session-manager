# csm

A Textual terminal UI for Claude Code sessions. The code is in `src/csm`, the tests in `tests`.

- Run the tests with `uv run pytest` (about four minutes; `uv run pytest tests/test_x.py` for one file).
- `tests/test_smoke.py` draws every screen and dialog with data built to break rendering: add new screens
  and dialogs there.
- Session titles, paths and results are user text: show them with `markup=False` or Rich `Text`, never as
  Textual markup.
- csm reads Claude Code's files and writes only its own unless the user asks; keep `docs/guide.md`'s
  "What it reads, writes and sends" true.
- Keep `README.md` short; the full reference is `docs/guide.md`.

## Delegating to subagents

Plan and review the work yourself; hand the doing to this project's subagents, which run on cheaper
models (they're in .claude/agents):

- **scout** (Haiku): finding where something is, what calls what, how the repo does something.
- **coder** (Sonnet): a change you can specify: the files or area, the behavior, how to check it.
  Run independent pieces in parallel.
- **tester** (Haiku): running the tests, linter and type checker and reporting failures.

Keep for yourself: design decisions, bugs whose cause is unknown, and anything about auth, secrets,
pricing or production config. Review what the agents report before calling the work done. When you use
an agent that isn't pinned, such as general-purpose, pass `model` explicitly: sonnet unless the task
needs Opus.
