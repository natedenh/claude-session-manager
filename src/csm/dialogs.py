"""The small dialogs: prompts, confirmations, the first message, when idle, help."""
from __future__ import annotations

import os

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.suggester import Suggester
from textual.widgets import Input, Label, Static, Switch, TextArea

from . import autoarchive


HELP = """\
[b]Navigate[/b]
  ↑↓ j k    move                          [ ]       previous / next project
  enter     open a session; on a project header, collapse / expand it
  S         summary of the last 48 hours: questions waiting on you, decisions, finished work
  e         show every session (5 per project otherwise)
  v         flat newest-first list / grouped by project

[b]Find[/b]
  /         filter by title, project, branch or note as you type; #tag matches tags
  s         search transcript text (enter runs it)
  esc       clear the filter, search and marks
  p w l !   only PR-linked / worktree / live / waiting-for-you (or stuck) sessions
  a         also show archived sessions (by csm, Claude desktop or the auto rule)

[b]Open[/b]
  enter     beside this list in tmux, else here (back to the list when claude exits).
            A session already open in a Ghostty tab or Claude desktop is shown there.
  ctrl+\\    cycle focus: this list, then the sessions beside it top to bottom (tmux);
            with none open, switch between this list and the preview
  n / N     new session in the highlighted project / in a new worktree. Asks for a first message:
            type one and ctrl+s to start it hidden while you stay here, or enter for an empty session
  P         new session in any directory (tab completes; offers to create a missing one)
  tab       open the session that has waited longest for you (permission first); shift+tab goes back
  ctrl+]    the same from anywhere, even inside a session (tmux): answer one, ctrl+], the next
  |         (tmux) show the highlighted session as a second pane; enter goes back to one
  R         (tmux) reply to the highlighted session without opening it
  f         fork the highlighted session
  B         continue fresh: a model writes a brief of the session, you edit it, and it opens a new one
            (named "<title> (continued)"); for when the context is nearly full
  o / O     resume in a new Ghostty tab / window
  c         (tmux) stop the session's claude process
  g         open the session's PR in the browser
  .         open the project in your editor ($CSM_EDITOR, code, cursor, or Finder)
  D         open the session in Claude desktop, even if it isn't running there

[b]Manage[/b]
  r         rename                        y         copy session id
  x         archive / unarchive           d         move transcript to the Trash
  X         retire: stop it (csm pane or background job) and archive (marked, or highlighted)
  @         when it goes idle: notify me, archive, retire, or (tmux) send it a prompt
  A         auto-archive rule (merged PRs, idle sessions); x keeps an auto-archived one
  E         export to Markdown (marked, or highlighted)
  #         edit tags (#waiting-on-chris; marked sessions get them added)
  i         edit a note; both show in the preview and are searched by /  (#tag)
  t         read the whole transcript (/ search, n N next / previous, g G top / bottom)
  L         what the session loaded: plugins, skills (✓ used), MCP servers, agents, hooks, CLAUDE.md files
  *         pin / unpin                   space     mark; x and d act on all marked
  $         costs                         W         clean up worktrees
  J         write today's recap to Markdown and open it: time, cost, PRs, what each session did
  I         stats: Claude time per day, streak, busiest projects, cost by week, hours, top skills
  ~         change the activity strip's style (or click it): wave, strands, equalizer, heartbeat, stars, knight rider
  ctrl+t    tips on / off: suggestions for features you aren't using, when they'd help
  ctrl+r    reload
  q         quit (in tmux: detach; sessions keep running)

[b]Icons[/b]
  [green]⇄[/] PR linked: [warn]pending[/], [red]failing[/], [magenta]merged[/], [dim]closed / draft[/]    [magenta]⑂[/] worktree   [dim]○[/] other
  [green]●[/] live, idle   [warn]●[/] live, busy   » shown beside the list
  on a project:  [warn]±3[/] uncommitted files   [cyan]↑2[/] commits to push   [dim]↓1[/] to pull   [dim]▁▃▇[/] active minutes, last 7 days
  ◆ waiting for you   [bold red]?[/] needs permission (with hooks); both show how long, ◆ 12m
  [warn]⧗[/] stuck: busy, but nothing written for 10+ minutes (--stuck-minutes)   ⑃ fork   [red]◔[/] context over 80%
  bg  a background job   [dim]⇢[/] a terminal attached to a background job (enter on the job shows it)
"""


class Prompt(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(self, title: str, value: str = "", allow_empty: bool = False):
        super().__init__()
        self.title_text, self.value, self.allow_empty = title, value, allow_empty

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, markup=False)  # titles and paths can hold brackets
            yield Input(value=self.value, select_on_focus=True)

    @on(Input.Submitted)
    def submit(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self.dismiss(text if text or self.allow_empty else None)

    def action_cancel(self) -> None:
        self.dismiss(None)


def complete_dir(value: str) -> str:
    """Extend a typed path as far as the directories on disk agree, adding / once it's unique."""
    parent, prefix = os.path.split(os.path.expanduser(value))
    try:
        names = sorted(e.name for e in os.scandir(parent or ".") if e.is_dir() and e.name.startswith(prefix)
                       and (prefix.startswith(".") or not e.name.startswith(".")))
    except OSError:
        return value
    if not names:
        return value
    common = os.path.commonprefix(names)
    return value + common[len(prefix):] + ("/" if len(names) == 1 else "")


class DirSuggester(Suggester):
    def __init__(self):
        super().__init__(use_cache=False)

    async def get_suggestion(self, value: str) -> str | None:
        done = complete_dir(value)
        return done if done != value else None


class DirPrompt(ModalScreen[str | None]):
    """Ask for a directory; tab (or →) takes the grey completion."""
    BINDINGS = [Binding("escape", "cancel", show=False), Binding("tab", "complete", show=False)]

    def __init__(self, title: str, value: str):
        super().__init__()
        self.title_text, self.value = title, value

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, markup=False)
            yield Input(value=self.value, suggester=DirSuggester())
            yield Label("[dim]tab = complete    enter = start    esc = cancel[/]")

    def action_complete(self) -> None:
        box = self.query_one(Input)
        box.value = complete_dir(box.value)
        box.cursor_position = len(box.value)

    @on(Input.Submitted)
    def submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class FirstMessage(ModalScreen[str | None]):
    """Ask what a new session should start on. "" starts it empty, as before; None cancels."""
    BINDINGS = [Binding("escape", "cancel", show=False), Binding("ctrl+s", "start", show=False)]
    DEFAULT_CSS = """
    FirstMessage TextArea { height: 8; margin-top: 1; }
    """

    class Box(TextArea):
        async def _on_key(self, event) -> None:
            if event.key == "enter" and not self.text.strip():  # nothing typed: start it plain
                event.stop()
                event.prevent_default()
                self.screen.dismiss("")
                return
            await super()._on_key(event)

    def __init__(self, where: str, text: str = ""):
        super().__init__()
        self.where, self.text = where, text

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(f"New session in {self.where}: first message ({'edit as you like' if self.text else 'optional'})",
                        markup=False)
            yield self.Box(self.text, soft_wrap=True, show_line_numbers=False)
            yield Label("[dim]ctrl+s = start; with a message it runs hidden and you stay here\n"
                        "enter on an empty box = start it beside the list    esc = cancel[/]")

    def action_start(self) -> None:
        self.dismiss(self.query_one(TextArea).text.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


class Confirm(ModalScreen[bool]):
    BINDINGS = [
        Binding("y", "answer(True)", show=False),
        Binding("enter", "answer(True)", show=False),
        Binding("n", "answer(False)", show=False),
        Binding("escape", "answer(False)", show=False),
    ]

    def __init__(self, message: str | Text):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.message, markup=False)
            yield Label("[dim]y / enter = yes    n / esc = no[/]")

    def action_answer(self, yes: bool) -> None:
        self.dismiss(yes)


class WhenIdle(ModalScreen[str | None]):
    """Pick what to do once a session goes idle: one key per choice."""
    BINDINGS = [Binding("n", "pick('notify')", show=False), Binding("a", "pick('archive')", show=False),
                Binding("r", "pick('retire')", show=False), Binding("s", "pick('send')", show=False),
                Binding("c", "pick('cancel')", show=False), Binding("escape", "pick", show=False)]

    def __init__(self, title: str, queued: str | None, can_send: bool):
        super().__init__()
        self.title_text, self.queued, self.can_send = title, queued, can_send

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(f"When “{self.title_text}” goes idle:", markup=False)
            yield Label("  n  notify me\n  a  archive it\n  r  retire it (stop and archive)"
                        + ("\n  s  send it a prompt" if self.can_send else ""))
            if self.queued:
                yield Label(f"  c  cancel the queued “{self.queued}”", markup=False)
            yield Label("[dim]esc = close[/]")

    def action_pick(self, choice: str | None = None) -> None:
        if choice == "send" and not self.can_send or choice == "cancel" and not self.queued:
            return
        self.dismiss(choice)


class AutoArchiveSettings(ModalScreen[dict | None]):
    BINDINGS = [Binding("escape", "cancel", show=False), Binding("ctrl+s", "save", show=False)]

    def __init__(self, rule: dict):
        super().__init__()
        self.rule = rule

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label("Auto-archive (never live, pinned or kept sessions)")
            with Horizontal(classes="row"):
                yield Switch(self.rule["enabled"], id="enabled")
                yield Label("enabled")
            yield Label("PR merged more than this many days ago")
            yield Input(str(self.rule["merged_days"]), type="integer", id="merged_days")
            yield Label("no activity for this many days")
            yield Input(str(self.rule["idle_days"]), type="integer", id="idle_days")
            yield Label("[dim]enter / ctrl+s = save    esc = cancel[/]")

    @on(Input.Submitted)
    def action_save(self) -> None:
        self.dismiss(autoarchive.normalize({
            "enabled": self.query_one("#enabled", Switch).value,
            **{k: int(v) if (v := self.query_one(f"#{k}", Input).value.strip()).isdigit() else None
               for k in ("merged_days", "idle_days")}}))

    def action_cancel(self) -> None:
        self.dismiss(None)


class Help(ModalScreen[None]):
    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(HELP)

    def on_key(self) -> None:
        self.dismiss(None)
