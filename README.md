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

`csm --archived` starts with archived sessions shown. Colors come from the terminal, and its background shows through. The default theme is `ansi-light` or `ansi-dark`, following macOS appearance. Override it with `--theme <name>` or `CSM_THEME`; any Textual theme name works, e.g. `textual-dark` for csm's own colors. A live session that finishes its turn is marked `◆` (bold) until you open it, and csm sends a desktop notification (OSC 9, which Ghostty shows as a macOS notification; not for the session shown beside the list). `csm --no-notify` turns the notifications off. `csm --no-tmux` skips tmux. Enter then resumes the session in this terminal, and you return to the list when claude exits. Add `--once` to exit instead.

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
| `esc` | clear the filter, search and marks |
| `p` `w` `l` `!` | only PR-linked / worktree / live / waiting sessions |
| `a` | also show archived sessions (dimmed) |
| `e` | show every session (5 per project by default) |
| `o` / `O` | resume in a new Ghostty tab / window (scripts your running Ghostty) |
| `n` / `N` | new session / new session in a worktree (`-w`), in the highlighted project; shown as a row until its transcript exists |
| `f` | fork the highlighted session (`--fork-session`), named "<title> (fork)" |
| `r` | rename (writes a `custom-title` record, like `/rename`) |
| `x` | archive / unarchive in csm (hidden here only; doesn't touch the desktop app) |
| `g` | open the session's PR in the browser |
| `.` | open the project directory in your editor: `$CSM_EDITOR`, else `code`, else `cursor`, else Finder |
| `D` | open the session in Claude desktop, even if it isn't running there (needs the app to know the session) |
| `x` | archive / unarchive in csm (hidden here only; doesn't touch the desktop app). On an auto-archived session it keeps the session instead |
| `A` | auto-archive settings (see below) |
| `y` | copy the session id |
| `d` | move the transcript to `~/.Trash` |
| `E` | export the highlighted (or marked) sessions to Markdown and reveal them in Finder |
| `W` | clean up worktrees (see below) |
| `$` | costs: totals, by project, by week, top sessions (`esc`/`q`/`$` closes) |
| `*` | pin / unpin a session (pinned sessions form a group at the top) |
| `v` | switch between the grouped view and a flat, newest-first list |
| `space` | mark / unmark a session; `x`, `d` and `E` then act on all marked sessions, `esc` clears the marks |
| `?` | help |

While any session is working, a wave moves along the bottom of the list next to a count ("2 working"). When everything is idle, it rests as a flat line and stops redrawing.

The preview header shows how full the context window is (`context ▰▰▰▰▰▱▱▱▱▱ 52%`, yellow past 50%, red past 80%), and rows above 80% get a red `◔` after the title; the window is 200k tokens, or 1M for `[1m]` models and any session already past 200k.

Forks are marked `⑃`, and the preview says which session they came from. A fork copies its original's conversation, title included, so csm links them by their shared first record.

Icons: `⇄` PR linked (colored by PR status, see below), magenta `⑂` worktree, `○` other. A dot in front means the session is running: green is idle, yellow is busy. `▶` marks the session shown beside the list. Opening a session that's running somewhere else switches to it where it's running. A Ghostty tab is found by its title and directory. A Claude desktop session is opened with `claude://code/continue?session=<id>`, which only works while the session is open in the app, because that's the only time its id is on disk. If neither applies, csm asks before resuming it a second time.

## PR status

For sessions linked to a pull request, csm asks `gh pr view` for its state, checks and review decision in the background (one PR at a time, newest session first) and colors the `⇄` icon: green is open with checks passing, yellow is checks pending or review required, red is checks failing or changes requested, magenta is merged, dim is closed or draft. The preview header shows the details, e.g. `open · checks 12/12 passing · approved`. Results are cached in `~/.cache/csm/prs.json`: open PRs refresh after 5 minutes, merged and closed ones at most daily. If `gh` is missing or not logged in, the icon stays plain green.

## Auto-archive

Off by default. `A` opens a dialog to turn on a rule that hides a session once its PR merged more than N days ago (default 7) or it has had no activity for N days (default 30). Live, pinned and kept sessions are never hidden. The rule is derived each time, not written into the archive: auto-archived sessions show dimmed with `a` and say why ("auto: PR merged 9d ago"), and the status bar counts them. `x` on one keeps it (the rule stops hiding it); `x` again archives it normally. The rule and kept ids are stored in `state.json`.

## Worktree cleanup

`W` lists every worktree under `<project>/.claude/worktrees/` for the projects csm knows, confirmed against what git has registered. Each row shows its branch, the sessions that used it, last activity, PR state (from the cache above), uncommitted files and commits not on any remote branch. Worktrees whose PR is merged or closed, or that saw no session activity for 14+ days, are flagged and sorted first; ones with a running session never are. `d` or `x` removes the selected one after a confirmation that warns about uncommitted or unpushed work. It runs plain `git worktree remove` (never `--force`), so git refuses a dirty worktree and csm shows its message. Registered worktrees whose directory is gone show as `missing`, and removing one runs `git worktree prune`. Branches are never deleted.

## Data

- **Sessions:** read from `~/.claude/projects/*/*.jsonl`. `CLAUDE_CONFIG_DIR` is honored.
- **Live status:** read from `~/.claude/sessions/<pid>.json`. Entries whose process has exited are ignored.
- **Parse cache:** `~/.cache/csm/index.json`, keyed on file mtime and size. The first run parses everything; after that only changed files are re-read.
- **csm's own state:** archived ids and collapsed projects, in `~/.local/state/csm/state.json`.
- **Claude desktop's archive:** read from `~/Library/Application Support/Claude*/claude-code-sessions/*/*/local_*.json`. Each record's `isArchived` applies to the transcript named by its `cliSessionId`. A session archived in either place is hidden until you press `a`. csm never changes the desktop app's archive.

- **Exports:** `E` writes `<date> <title>.md` (front matter, then the whole conversation) to `~/Downloads/claude-sessions/`. Change the folder with `--export-dir` or `CSM_EXPORT_DIR`. Re-exporting a session overwrites its file; a different session with the same name gets ` (2)`.

Only rename and delete touch Claude's files. Rename appends one record to the transcript; delete moves the transcript to the Trash.

Sessions are grouped by the directory they were launched in, or the directory they were relocated to. Worktrees fold into their repo. A `cd` during the session doesn't regroup it.

## Development

```sh
uv run pytest
```
