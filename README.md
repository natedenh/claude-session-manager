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
| `R` | (tmux) reply to the highlighted session without opening it: types your text and Enter into its pane |
| `\|` | (tmux) show the highlighted session as a second pane below the current one; `enter` on any session goes back to one |
| `c` | (tmux) stop the session's claude process |
| `/` | filter by title, project, branch or note as you type; `#tag` matches sessions with that tag (prefix match) |
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
| `t` | read the whole transcript; `/` searches, `n` / `N` step through matches, `g` / `G` top / bottom, `esc` closes search then the viewer |
| `W` | clean up worktrees (see below) |
| `$` | costs: totals, by project, by week, top sessions (`esc`/`q`/`$` closes) |
| `*` | pin / unpin a session (pinned sessions form a group at the top) |
| `#` | edit tags (`#waiting-on-chris #blocked`) for the highlighted session; for marked sessions the tags are added to each. Shown dim after the title |
| `i` | edit a one-line note for the highlighted session (empty removes it). Tags and note show in the preview |
| `v` | switch between the grouped view and a flat, newest-first list |
| `space` | mark / unmark a session; `x`, `d` and `E` then act on all marked sessions, `esc` clears the marks |
| `?` | help |

While any session is working, a thin wave moves along the bottom of the list next to a count ("2 working"), with a coral spark sweeping across it (two when three or more sessions are working). The wave rises when work starts, grows a little livelier with more sessions, and settles back to a flat line when everything is idle, at which point it stops redrawing.

The preview header shows how full the context window is (`context ▰▰▰▰▰▱▱▱▱▱ 52%`, yellow past 50%, red past 80%), and rows above 80% get a red `◔` after the title; the window is 200k tokens, or 1M for `[1m]` models and any session already past 200k.

Forks are marked `⑃`, and the preview says which session they came from. A fork copies its original's conversation, title included, so csm links them by their shared first record.

Icons: `⇄` PR linked (colored by PR status, see below), magenta `⑂` worktree, `○` other. A dot in front means the session is running: green is idle, yellow is busy (and pulses slowly while it works). `»` marks every session shown beside the list. `◆` means it finished a turn and is waiting for you; a red `?` means it needs permission (needs the hooks below). Opening a session that's running somewhere else switches to it where it's running. A Ghostty tab is found by its title and directory. A Claude desktop session is opened with `claude://code/continue?session=<id>`, which only works while the session is open in the app, because that's the only time its id is on disk. If neither applies, csm asks before resuming it a second time.

## Summary

The `◎ Summary` row at the top of the list (or `S` from anywhere) covers the last 48 hours across all sessions: **Needs you** (the question each waiting session ended on, and permission requests), **Decisions**, **Finished** work, and what's **Still running**. In the summary screen, `enter` opens the session an item came from.

Each recently active session that has settled (idle, unchanged for a minute) is summarized by Claude Haiku 4.5 from its last 16 turns, one session at a time in the background, and cached in `~/.cache/csm/summaries.json` until its transcript changes. csm calls Claude the way Claude Code is configured to: when `~/.claude/settings.json` sets `CLAUDE_CODE_USE_BEDROCK`, it uses Bedrock with that file's `AWS_PROFILE`, `AWS_REGION` and `ANTHROPIC_DEFAULT_HAIKU_MODEL`; otherwise the Anthropic API. `CSM_SUMMARY_MODEL` overrides the model. If a call fails (for example an expired SSO login), the page says so and csm retries after 5 minutes. Without summaries, "Needs you" still works from each session's last message.

## PR status

For sessions linked to a pull request, csm asks `gh pr view` for its state, checks and review decision in the background (one PR at a time, newest session first) and colors the `⇄` icon: green is open with checks passing, yellow is checks pending or review required, red is checks failing or changes requested, magenta is merged, dim is closed or draft. The preview header shows the details, e.g. `open · checks 12/12 passing · approved`. Results are cached in `~/.cache/csm/prs.json`: open PRs refresh after 5 minutes, merged and closed ones at most daily. If `gh` is missing or not logged in, the icon stays plain green.

## Auto-archive

Off by default. `A` opens a dialog to turn on a rule that hides a session once its PR merged more than N days ago (default 7) or it has had no activity for N days (default 30). Live, pinned and kept sessions are never hidden. The rule is derived each time, not written into the archive: auto-archived sessions show dimmed with `a` and say why ("auto: PR merged 9d ago"), and the status bar counts them. `x` on one keeps it (the rule stops hiding it); `x` again archives it normally. The rule and kept ids are stored in `state.json`.

## Worktree cleanup

`W` lists every worktree under `<project>/.claude/worktrees/` for the projects csm knows, confirmed against what git has registered. Each row shows its branch, the sessions that used it, last activity, PR state (from the cache above), uncommitted files and commits not on any remote branch. Worktrees whose PR is merged or closed, or that saw no session activity for 14+ days, are flagged and sorted first; ones with a running session never are. `d` or `x` removes the selected one after a confirmation that warns about uncommitted or unpushed work. It runs plain `git worktree remove` (never `--force`), so git refuses a dirty worktree and csm shows its message. Registered worktrees whose directory is gone show as `missing`, and removing one runs `git worktree prune`. Branches are never deleted.

## Hooks (optional)

By default csm infers "waiting" from a session's busy-to-idle change, which can't tell a finished turn from a permission prompt. Claude Code hooks can. `csm hooks install` adds four entries to `~/.claude/settings.json` (`--settings PATH` for another file):

```json
{"hooks": {
  "Notification":     [{"hooks": [{"type": "command", "command": "<python> -m csm hook"}]}],
  "Stop":             [{"hooks": [{"type": "command", "command": "<python> -m csm hook"}]}],
  "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "<python> -m csm hook"}]}],
  "SessionEnd":       [{"hooks": [{"type": "command", "command": "<python> -m csm hook"}]}]
}}
```

It shows the changes and asks first (`--yes` skips the question), leaves your other hooks and settings alone, backs the file up as `settings.json.bak-csm-<timestamp>`, and does nothing if the entries are already there. `csm hooks uninstall` removes only entries whose command contains `csm hook`; `csm hooks status` shows what's installed. Already-running sessions pick up new hooks only after they restart.

The hook writes `~/.local/state/csm/status/<session id>.json` (removed at session end) and prints nothing. With it, a session asking for permission gets a red `?` (the preview shows what it asked for, the status bar says "N need permission", `!` includes it, and a notification says "<title> needs permission"), and a finished turn is marked `◆` immediately. Opening the session clears the mark. Without hook files csm behaves as before.

## Data

- **Sessions:** read from `~/.claude/projects/*/*.jsonl`. `CLAUDE_CONFIG_DIR` is honored.
- **Live status:** read from `~/.claude/sessions/<pid>.json`. Entries whose process has exited are ignored.
- **Parse cache:** `~/.cache/csm/index.json`, keyed on file mtime and size. The first run parses everything; after that only changed files are re-read.
- **csm's own state:** archived ids, collapsed projects, pinned sessions, the flat-view setting, and session tags and notes, in `~/.local/state/csm/state.json`.
- **Claude desktop's archive:** read from `~/Library/Application Support/Claude*/claude-code-sessions/*/*/local_*.json`. Each record's `isArchived` applies to the transcript named by its `cliSessionId`. A session archived in either place is hidden until you press `a`, except while it's working, waiting on you or shown beside the list: then it reappears, dimmed, and hides again once it's settled. csm never changes the desktop app's archive.

- **Exports:** `E` writes `<date> <title>.md` (front matter, then the whole conversation) to `~/Downloads/claude-sessions/`. Change the folder with `--export-dir` or `CSM_EXPORT_DIR`. Re-exporting a session overwrites its file; a different session with the same name gets ` (2)`.

Only rename and delete touch Claude's files. Rename appends one record to the transcript; delete moves the transcript to the Trash.

Sessions are grouped by the directory they were launched in, or the directory they were relocated to. Worktrees fold into their repo. A `cd` during the session doesn't regroup it.

## Development

```sh
uv run pytest
```
