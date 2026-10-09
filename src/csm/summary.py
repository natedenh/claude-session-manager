"""The Summary page: what needs you, what was decided, what got done, what's running.

Each recently active session's tail is summarized by a small model (Claude Haiku 5.5) into a
structured digest, cached until its transcript changes. The page itself is assembled from
those digests plus live state, so it renders instantly and works without any model at all.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, Field

from . import data
from .data import LiveSession, Paths, Session

WINDOW = 48 * 3600
SETTLE = 60  # don't summarize a transcript that changed in the last minute; it's still moving
RETRY_AFTER = 300  # after a failure (e.g. expired SSO), wait before trying again
TURNS = 16
TURN_CHARS = 1500
PROMPT_VERSION = 2  # bump when SYSTEM changes, so cached digests are redone

SYSTEM = """You read the end of a Claude Code session and summarize it for the person who owns it.
They run many sessions in parallel and use your summary to decide where to look next.

Report:
- status: "needs_input" if the assistant's last message asks the user something or waits on their decision or action; "done" if the task it was working on is finished; "in_progress" if work is underway; "idle" if nothing is pending.
- question: when status is needs_input, the question or request to the user in one sentence, phrased as the assistant asked it. Otherwise null.
- decisions: up to 3 choices the user made or explicitly approved in these turns, each a short clause naming what was chosen, e.g. "use sub, not email, as the lookup key". A decision is a choice between options. Finished work is not a decision (put it in done), and neither is the assistant's own plan.
- done: up to 3 concrete results completed in these turns (merged or opened PRs, fixes committed, answers delivered), each a short clause with identifiers like PR numbers.
- headline: at most 12 words on what the session is about right now.

Use only what the transcript says. Leave a list empty rather than guess."""


class Digest(BaseModel):
    status: Literal["needs_input", "done", "in_progress", "idle"]
    question: str | None = None
    decisions: list[str] = Field(default_factory=list)
    done: list[str] = Field(default_factory=list)
    headline: str = ""


@dataclass
class Item:
    session: Session
    text: str
    when: float


@dataclass
class Page:
    needs_you: list[Item] = field(default_factory=list)
    decisions: list[Item] = field(default_factory=list)
    finished: list[Item] = field(default_factory=list)
    running: list[Item] = field(default_factory=list)
    pending: int = 0  # sessions still waiting for a digest
    error: str | None = None


# ---- model access -----------------------------------------------------------

def claude_env() -> dict[str, str]:
    """Claude Code's own `env` settings: the provider, AWS profile and region it uses."""
    try:
        return json.loads((Paths().claude / "settings.json").read_text()).get("env") or {}
    except (OSError, ValueError):
        return {}


def make_client():
    """Talk to Claude the same way Claude Code does: Bedrock if it's set up for Bedrock."""
    import anthropic  # deferred: only needed once a summary is actually requested

    env = {**claude_env(), **os.environ}
    if env.get("CLAUDE_CODE_USE_BEDROCK") in ("1", "true"):
        # The bedrock-runtime client: on Bedrock's Mantle endpoint, Haiku 4.5 rejects structured output.
        client = anthropic.AnthropicBedrock(
            aws_region=env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or "us-east-1",
            aws_profile=env.get("AWS_PROFILE"))
        model = env.get("CSM_SUMMARY_MODEL") or env.get("ANTHROPIC_DEFAULT_HAIKU_MODEL") \
            or "us.anthropic.claude-haiku-5-5"
        return client, model
    return anthropic.Anthropic(), env.get("CSM_SUMMARY_MODEL", "claude-haiku-5-5")


def render_turns(s: Session) -> str:
    lines = [f"Session: {s.title} (project {s.project_name})"]
    for m in data.transcript(s.path, limit=TURNS):
        text = m.text if len(m.text) <= TURN_CHARS else m.text[:TURN_CHARS] + " …"
        who = {"user": "USER", "assistant": "ASSISTANT"}.get(m.role)
        lines.append(f"[ran {m.text} tool calls]" if who is None else f"{who}: {text}")
    return "\n\n".join(lines)


def summarize(client, model: str, s: Session) -> Digest:
    response = client.messages.parse(
        model=model,
        max_tokens=1024,
        system=SYSTEM,
        messages=[{"role": "user", "content": render_turns(s)}],
        output_format=Digest,
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise RuntimeError(f"no summary ({response.stop_reason})")
    return response.parsed_output


# ---- cache ------------------------------------------------------------------

class Digests:
    """{session id: digest}, keyed on the transcript's mtime and size so edits refresh it."""

    def __init__(self, path: Path):
        self.path = path
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            raw = {}
        self.entries: dict[str, dict] = raw.get("sessions", {})
        self.error: str | None = raw.get("error")
        self.failed_at: float = raw.get("failed_at", 0.0)

    def get(self, s: Session) -> Digest | None:
        e = self.entries.get(s.id)
        if not e:
            return None
        try:
            return Digest(**e["digest"])
        except (TypeError, ValueError):
            return None

    def fresh(self, s: Session) -> bool:
        e = self.entries.get(s.id)
        return bool(e) and e.get("mtime") == s.mtime and e.get("size") == s.size and e.get("v") == PROMPT_VERSION

    def put(self, s: Session, d: Digest) -> None:
        self.entries[s.id] = {"mtime": s.mtime, "size": s.size, "v": PROMPT_VERSION, "digest": d.model_dump()}
        self.error, self.failed_at = None, 0.0

    def fail(self, message: str, now: float) -> None:
        self.error, self.failed_at = message, now

    def save(self) -> None:
        data._write_json(self.path, {"sessions": self.entries, "error": self.error, "failed_at": self.failed_at})


def due(sessions: list[Session], digests: Digests, live: dict[str, LiveSession], now: float) -> list[Session]:
    """Recently active, settled sessions whose digest is missing or stale; newest first."""
    return [s for s in sessions
            if now - s.mtime < WINDOW and now - s.mtime > SETTLE and not digests.fresh(s)
            and (live.get(s.id) is None or live[s.id].status == "idle")]


def refresh(paths: Paths, sessions: list[Session], live: dict[str, LiveSession], now: float | None = None,
            cancelled: Callable[[], bool] = lambda: False, client=None) -> Digests:
    """Summarize what's due, one session at a time, saving after each."""
    now = time.time() if now is None else now
    digests = Digests(paths.summaries)
    todo = due(sessions, digests, live, now)
    if not todo or (digests.error and now - digests.failed_at < RETRY_AFTER):
        return digests
    try:
        model = None
        if client is None:
            client, model = make_client()
        model = model or "claude-haiku-5-5"
        for s in todo:
            if cancelled():
                break
            digests.put(s, summarize(client, model, s))
            digests.save()
    except Exception as e:  # credentials, network, quota: show it on the page and back off
        digests.fail(f"{type(e).__name__}: {e}"[:300], now)
        digests.save()
    return digests


# ---- the page -----------------------------------------------------------------

def build(sessions: list[Session], live: dict[str, LiveSession], waiting: set[str], permission: dict[str, str],
          digests: Digests, last_said: Callable[[Session], str | None], now: float | None = None,
          hidden: Callable[[Session], bool] = lambda s: False) -> Page:
    now = time.time() if now is None else now
    page = Page(error=digests.error)
    for s in sessions:  # newest first
        if hidden(s) or now - s.mtime > WINDOW and s.id not in live:
            continue
        d = digests.get(s) if digests.fresh(s) else None
        state = live.get(s.id)
        if s.id in permission:
            page.needs_you.append(Item(s, f"needs permission: {permission[s.id]}", s.mtime))
        elif s.id in waiting or (d and d.status == "needs_input"):
            page.needs_you.append(Item(s, (d.question if d and d.question else last_said(s)) or "waiting for you", s.mtime))
        elif state and state.status != "idle":
            page.running.append(Item(s, (d.headline if d and d.headline else last_said(s)) or "working", s.mtime))
        if d:
            page.decisions += [Item(s, x, s.mtime) for x in d.decisions]
            page.finished += [Item(s, x, s.mtime) for x in d.done]
        elif now - s.mtime < WINDOW and not digests.fresh(s):
            page.pending += 1
    return page


# ---- rendering --------------------------------------------------------------

SECTIONS = (("needs_you", "Needs you", "warn.bold"), ("decisions", "Decisions", "bold cyan"),
            ("finished", "Finished", "bold green"), ("running", "Still running", "bold"))


def counts(page: Page) -> str:
    parts = [f"{len(page.needs_you)} need you"] if page.needs_you else []
    if page.finished:
        parts.append(f"{len(page.finished)} done")
    if page.pending:
        parts.append(f"summarizing {page.pending}…")
    return " · ".join(parts)


def footnote(page: Page):
    from rich.text import Text
    if page.error:
        hint = " (run `aws sso login`?)" if "token" in page.error.lower() or "credential" in page.error.lower() else ""
        return Text(f"Summaries unavailable: {page.error}{hint}", style="red")
    if page.pending:
        return Text(f"Summarizing {page.pending} more session{'s' if page.pending != 1 else ''} in the background…", style="dim")
    return None


def render(page: Page, ago: Callable[[float], str]):
    """The page as Rich renderables, for the preview pane."""
    from rich.console import Group
    from rich.text import Text
    out = [Text("Summary · last 48 hours", style="bold"), Text("")]
    for key, title, style in SECTIONS:
        items = getattr(page, key)
        if not items:
            continue
        out.append(Text(title, style=style))
        for it in items:
            out.append(Text.assemble(("  • ", "dim"), it.text, (f"   {it.session.title} · {ago(it.when)}", "dim")))
        out.append(Text(""))
    if not any(getattr(page, k) for k, _, _ in SECTIONS):
        out.append(Text("Nothing in the last 48 hours.", style="dim"))
    if note := footnote(page):
        out.append(note)
    return Group(*out)


def screen(page: Page, ago: Callable[[float], str]):
    """A full screen listing every item; enter returns the item's session."""
    from rich.text import Text
    from textual.app import ComposeResult
    from textual.binding import Binding
    from textual.screen import Screen
    from textual.widgets import Footer, OptionList, Static
    from textual.widgets.option_list import Option

    class SummaryScreen(Screen):
        BINDINGS = [Binding("escape", "close", "Close"), Binding("q", "close", show=False),
                    Binding("S", "close", show=False), Binding("j", "down", show=False),
                    Binding("k", "up", show=False)]
        DEFAULT_CSS = """
        SummaryScreen #title { padding: 0 2; text-style: bold; }
        SummaryScreen OptionList { border: none; height: 1fr; text-wrap: nowrap; text-overflow: ellipsis; }
        SummaryScreen #note { padding: 0 2; height: auto; }
        """

        def compose(self) -> ComposeResult:
            yield Static(Text.assemble(("Summary", "bold"), ("  last 48 hours · enter opens the session", "dim")), id="title")
            options, self.items = [], {}
            for key, title, style in SECTIONS:
                items = getattr(page, key)
                if not items:
                    continue
                if options:
                    options.append(Option("", disabled=True))
                options.append(Option(Text(title, style=style), disabled=True))
                for i, it in enumerate(items):
                    oid = f"{key}:{i}"
                    self.items[oid] = it.session
                    options.append(Option(Text.assemble(("  ", ""), it.text,
                                                        (f"   {it.session.title} · {ago(it.when)}", "dim")), id=oid))
            if not self.items:
                options.append(Option(Text("Nothing in the last 48 hours.", style="dim"), disabled=True))
            yield OptionList(*options)
            yield Static(footnote(page) or "", id="note")
            yield Footer()

        def on_mount(self) -> None:
            lst = self.query_one(OptionList)
            lst.highlighted = next((i for i, o in enumerate(lst.options) if not o.disabled), None)
            lst.focus()

        def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
            self.dismiss(self.items.get(event.option.id))

        def action_down(self) -> None:
            self.query_one(OptionList).action_cursor_down()

        def action_up(self) -> None:
            self.query_one(OptionList).action_cursor_up()

        def action_close(self) -> None:
            self.dismiss(None)

    return SummaryScreen()
