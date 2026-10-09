# Contributing

csm is built for one person's workflow and shared in case it helps yours. Issues and pull requests are welcome; there are no promises on how quickly they're answered.

- **Bugs:** say what you did, what you expected, and what happened. If the sidebar crashed, include the newest entry from `~/.local/state/csm/crash.log`.
- **Pull requests:** fork the repo and open a pull request against `main`. Keep each one to a single change, and add or update tests for it. CI must pass, and the maintainer reviews every pull request before it's merged.
- **Running the tests:** `uv run pytest`. `tests/test_smoke.py` draws every screen, dialog and notification with data built to break rendering; if you add a screen, add it there too.
- **Platforms:** csm is developed on macOS. Changes that keep the core working elsewhere (the tests run on Linux in CI) are welcome.
- **Claude Code changes:** csm reads Claude Code's own files, which aren't a public API. If an update breaks something, an issue with the new format, with any private details removed, helps most.
