# Claude Code Session Manager

The desktop app has some great features, but I am more productive in the cli, so I made `csm`: a terminal UI for Claude Code sessions. It has the desktop app's sidebar, plus search, filters, and resume.

![csm in tmux: the session list on the left, a Claude Code session running beside it](docs/screenshots/sidebar.svg)

An independent project, not made by or affiliated with Anthropic. It reads Claude Code's own files, which aren't a public API, so a Claude Code update can break something until csm catches up.

## Install

You need [Claude Code](https://code.claude.com), [uv](https://docs.astral.sh/uv/) and, for the sidebar, tmux.

```sh
brew install tmux
uv tool install git+https://github.com/natedenh/claude-session-manager
csm
```

csm is built on macOS; the core runs anywhere tmux does. Optional: a logged-in `gh` colors each PR by its status, and `csm hooks install` makes the waiting and permission marks exact.

## What it does

- **Every session in one list**, grouped by project, newest first, with a preview of the last few turns. Filter as you type, search transcript text, tag, pin, note and archive.
- **Sessions run beside the list**, like the desktop app's window. Opening another swaps it in; the last one keeps running out of sight. Close the terminal and they keep running; after a reboot, `csm` reopens the ones you had open.
- **What needs you, first.** A session that finished its turn is marked `◆` with how long it has waited, one asking permission gets a red `?`, and one that's gone quiet mid-task gets `⧗`. `tab` opens whichever has waited longest; `ctrl+]` does it from inside a session.
- **Start work without stopping.** `n` starts a new session on a first message while you stay in the list; `B` continues a session whose context is nearly full in a fresh one, from a brief of where it stands.
- **A summary of the last 48 hours:** what needs you, what was decided, what got done.

![The Summary page: what needs you, decisions, finished work, what's running](docs/screenshots/summary.svg)

- **Stats and recaps.** Claude time, cost by day, project and model (with what the same work would cost on Sonnet or Haiku), your streak and most-used skills. `J` writes the day as a Markdown note.

![The stats screen](docs/screenshots/stats.svg)

- **And more:** PR status on each session, git state and a week of activity on each project, the Claude desktop app's routines and their results, worktree cleanup, what each session loaded, exports, and tips for features you haven't tried.

## Keys you'll use most

| Key | |
| --- | --- |
| `enter` | open the session beside the list |
| `ctrl+\` | move between the list and the sessions beside it |
| `tab` | open the session that has waited longest for you; `ctrl+]` from inside a session |
| `/` · `s` | filter as you type · search transcript text |
| `n` · `P` | new session in this project · in any folder |
| `B` | continue a long session fresh, from a brief |
| `\|` | show a second session below the first |
| `R` | reply to a session without opening it |
| `x` · `*` · `#` | archive · pin · tag |
| `t` | read the whole transcript |
| `S` · `I` · `J` | summary · stats · today's recap |
| `?` | every key |
| `q` | detach; sessions keep running |

The [guide](docs/guide.md) covers every key, icon, option and file.

## Privacy

csm reads Claude Code's files and writes only its own, under `~/.cache/csm` and `~/.local/state/csm`. It changes Claude's files only when you ask: renaming a session, moving one to the Trash, or installing the hooks. Nothing leaves your machine except PR lookups through `gh`, and, for the Summary page and `B`, the end of a session sent to Claude through your own Claude Code provider. No telemetry. [Details](docs/guide.md#what-it-reads-writes-and-sends).

## Development

```sh
uv tool install --editable .   # so `csm` runs your checkout
uv run pytest
```

CI runs the tests on every push. `tests/test_smoke.py` draws every screen and dialog with data built to break rendering; add new screens there. `scripts/screenshots.py` and `scripts/screenshot_tmux.py` regenerate the screenshots from made-up sessions. Issues and pull requests are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT, see [LICENSE](LICENSE).
