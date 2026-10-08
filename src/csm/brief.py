"""A brief for continuing a session in a fresh one: the opening message, written from its transcript.

Useful when a session's context is nearly full: the new session starts clean but knows the
goal, where things stand, what was decided and what's next.
"""
from __future__ import annotations

import os

from . import data
from .data import Session
from .summary import claude_env

TURNS = 40
TURN_CHARS = 2000

SYSTEM = """You write the first message of a new Claude Code session that takes over from an earlier one.
The new session sees nothing but your message, so it must be able to carry on from it alone.

Write it to Claude, in the second person, as the user would hand the work over. Cover:
- the goal, in a sentence or two;
- where things stand: what is done and what is half-done, naming files, branches, PRs, commits, commands;
- decisions the user made or approved, and constraints or preferences they stated;
- open questions, and anything that was failing or uncertain;
- the next step to take.

Use only what the transcript says, and keep its identifiers exact. Under 400 words, in short
paragraphs or bullets. No preamble, no sign-off."""


def model_for(env: dict[str, str]) -> str:
    if env.get("CSM_BRIEF_MODEL"):
        return env["CSM_BRIEF_MODEL"]
    if env.get("CLAUDE_CODE_USE_BEDROCK") in ("1", "true"):
        return env.get("ANTHROPIC_DEFAULT_OPUS_MODEL") or "us.anthropic.claude-opus-5-5"
    return "claude-opus-5-5"


def make_client():
    import anthropic  # deferred, as in summary

    env = {**claude_env(), **os.environ}
    if env.get("CLAUDE_CODE_USE_BEDROCK") in ("1", "true"):
        client = anthropic.AnthropicBedrock(
            aws_region=env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or "us-east-1",
            aws_profile=env.get("AWS_PROFILE"))
    else:
        client = anthropic.Anthropic()
    return client, model_for(env)


def render_turns(s: Session) -> str:
    lines = [f"Session: {s.title} (project {s.project_name}, directory {s.cwd}"
             + (f", branch {s.branch}" if s.branch else "") + (f", PR {s.pr_url}" if s.pr_url else "") + ")"]
    for m in data.transcript(s.path, limit=TURNS):
        text = m.text if len(m.text) <= TURN_CHARS else m.text[:TURN_CHARS] + " …"
        who = {"user": "USER", "assistant": "ASSISTANT"}.get(m.role)
        lines.append(f"[ran {m.text} tool calls]" if who is None else f"{who}: {text}")
    return "\n\n".join(lines)


def write(s: Session, client=None, model: str | None = None) -> str:
    """The brief, ending with where the old transcript is, for anything it left out."""
    if client is None:
        client, model = make_client()
    response = client.messages.create(
        model=model or "claude-opus-5-5",
        max_tokens=8000,
        system=SYSTEM,
        messages=[{"role": "user", "content": render_turns(s)}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("the model declined to write a brief")
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise RuntimeError(f"no brief ({response.stop_reason})")
    return f"{text}\n\nThe previous session's transcript is at {s.path} if you need more detail."
