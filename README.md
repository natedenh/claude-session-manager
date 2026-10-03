# claude-session-manager

`csm` is a terminal UI for Claude Code sessions: the desktop app's sidebar, plus search, filters, and resume.

It lists every session in `~/.claude/projects`, grouped by project with the most recent activity first. A preview pane shows the session's details and its last few turns. Press enter to resume a session with `claude -r` in the right directory. When claude exits you're back in the list, on the same session.

## Install

```sh
uv tool install --editable .
csm
```

Or run it without installing: `uv run csm`. Use `csm --once` to exit after resuming instead of returning to the list.

## Keys

| Key | Action |
| --- | --- |
| `↑↓` / `j k`, `[ ]` | move; jump to the previous/next project |
| `enter` | resume the session, or collapse/expand a project header |
| `/` | filter by title, project or branch as you type |
| `s` | search transcript text (runs on enter) |
| `esc` | clear the filter and search |
| `p` `w` `l` | only PR-linked / worktree / live sessions |
| `a` | show archived sessions instead |
| `e` | show every session (5 per project by default) |
| `o` / `O` | resume in a new Ghostty tab / window (scripts your running Ghostty) |
| `r` | rename (writes a `custom-title` record, like `/rename`) |
| `x` | archive / unarchive (hides it here only) |
| `y` | copy the session id |
| `d` | move the transcript to `~/.Trash` |
| `?` | help |

Icons: green `⇄` PR linked, magenta `⑂` worktree, `○` other. A dot in front means the session is running: green is idle, yellow is busy. If you resume a session that's already running, csm asks first.

## Data

- **Sessions:** read from `~/.claude/projects/*/*.jsonl`. `CLAUDE_CONFIG_DIR` is honored.
- **Live status:** read from `~/.claude/sessions/<pid>.json`. Entries whose process has exited are ignored.
- **Parse cache:** `~/.cache/csm/index.json`, keyed on file mtime and size. The first run parses everything; after that only changed files are re-read.
- **csm's own state:** archived ids and collapsed projects, in `~/.local/state/csm/state.json`.

Only rename and delete touch Claude's files. Rename appends one record to the transcript; delete moves the transcript to the Trash.

Sessions are grouped by the directory they were launched in, or the directory they were relocated to. Worktrees fold into their repo. A `cd` during the session doesn't regroup it.

## Development

```sh
uv run pytest
```
