# claude-session-manager

`csm` is a terminal UI for Claude Code sessions: the desktop app's sidebar, plus search, filters, and resume.

It lists every session in `~/.claude/projects`, grouped by project with the most recent activity first. A preview pane shows the session's details and its last few turns, rendered as Markdown. While a transcript search is active, the turns are shown as plain text so the matches can be highlighted.

When tmux is installed, `csm` works like the desktop app's window: the list is a sidebar on the left and the session you open runs on the right. Opening another session swaps it in. The previous one keeps running out of sight, and switching back to it is instant. `ctrl+\` moves focus between the sidebar and the session, and clicking either side works too. A session that's already open in another Ghostty tab or in the Claude desktop app is shown there instead of being resumed twice.

## Install

```sh
uv tool install --editable .
csm
```

Or run it without installing: `uv run csm`. For the side-by-side mode, `brew install tmux`.

`csm --archived` starts with archived sessions shown. Colors come from the terminal, and its background shows through. The default theme is `ansi-light` or `ansi-dark`, following macOS appearance. Override it with `--theme <name>` or `CSM_THEME`; any Textual theme name works, e.g. `textual-dark` for csm's own colors. `csm --no-tmux` skips tmux. Enter then resumes the session in this terminal, and you return to the list when claude exits. Add `--once` to exit instead.

### How the tmux mode works

`csm` starts or reattaches to a private tmux server (`tmux -L csm`) that uses this package's `src/csm/tmux.conf`. Your own tmux config and sessions are untouched. That config sets no prefix key, because Claude Code uses `ctrl+b`. It turns on the mouse and hides the status bar.

`q` detaches. Your sessions keep running, and closing the Ghostty window does the same thing. Running `csm` again brings everything back. To stop a session's claude process, press `c` on it in the list, or exit claude as usual. `tmux -L csm kill-server` stops everything.

Inside your own tmux, `csm` uses the current window instead, and `q` quits rather than detaching your client.

## Keys

| Key | Action |
| --- | --- |
| `↑↓` / `j k`, `[ ]` | move; jump to the previous/next project |
| `enter` | open the session (beside the list in tmux, else in this terminal), or collapse/expand a project header |
| `ctrl+\` | (tmux) switch focus between the list and the session |
| `c` | (tmux) stop the session's claude process |
| `/` | filter by title, project or branch as you type |
| `s` | search transcript text (runs on enter) |
| `esc` | clear the filter and search |
| `p` `w` `l` | only PR-linked / worktree / live sessions |
| `a` | also show archived sessions (dimmed) |
| `e` | show every session (5 per project by default) |
| `o` / `O` | resume in a new Ghostty tab / window (scripts your running Ghostty) |
| `n` / `N` | new session / new session in a worktree (`-w`), in the highlighted project; shown as a row until its transcript exists |
| `f` | fork the highlighted session (`--fork-session`) |
| `r` | rename (writes a `custom-title` record, like `/rename`) |
| `x` | archive / unarchive in csm (hidden here only; doesn't touch the desktop app) |
| `y` | copy the session id |
| `d` | move the transcript to `~/.Trash` |
| `?` | help |

Icons: green `⇄` PR linked, magenta `⑂` worktree, `○` other. A dot in front means the session is running: green is idle, yellow is busy. `▶` marks the session shown beside the list. Opening a session that's running somewhere else switches to it where it's running. A Ghostty tab is found by its title and directory. A Claude desktop session is opened with `claude://code/continue?session=<id>`, which only works while the session is open in the app, because that's the only time its id is on disk. If neither applies, csm asks before resuming it a second time.

## Data

- **Sessions:** read from `~/.claude/projects/*/*.jsonl`. `CLAUDE_CONFIG_DIR` is honored.
- **Live status:** read from `~/.claude/sessions/<pid>.json`. Entries whose process has exited are ignored.
- **Parse cache:** `~/.cache/csm/index.json`, keyed on file mtime and size. The first run parses everything; after that only changed files are re-read.
- **csm's own state:** archived ids and collapsed projects, in `~/.local/state/csm/state.json`.
- **Claude desktop's archive:** read from `~/Library/Application Support/Claude*/claude-code-sessions/*/*/local_*.json`. Each record's `isArchived` applies to the transcript named by its `cliSessionId`. A session archived in either place is hidden until you press `a`. csm never changes the desktop app's archive.

Only rename and delete touch Claude's files. Rename appends one record to the transcript; delete moves the transcript to the Trash.

Sessions are grouped by the directory they were launched in, or the directory they were relocated to. Worktrees fold into their repo. A `cd` during the session doesn't regroup it.

## Development

```sh
uv run pytest
```
