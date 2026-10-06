"""What a session had loaded: plugins, skills, MCP servers, agents, hooks and instruction files.

Claude Code records these in the transcript as attachments: a listing when the session
starts (or resumes), then deltas as things change. The current set is the latest initial
listing plus the deltas after it. Skills used are counted over the whole transcript.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field

from rich.console import Group
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static

from .data import Session

COMMAND = re.compile(r"<command-name>/?([^<]+)</command-name>")


@dataclass
class Loadout:
    skills: list[str] = field(default_factory=list)
    used: Counter = field(default_factory=Counter)  # skill name: times used
    mcp: list[str] = field(default_factory=list)
    failed_mcp: list[str] = field(default_factory=list)
    pending_mcp: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    hooks: Counter = field(default_factory=Counter)  # hook name: times it ran
    instructions: list[str] = field(default_factory=list)
    versions: dict[str, str] = field(default_factory=dict)  # plugin: version, from skills that were used

    @property
    def found(self) -> bool:
        return bool(self.skills or self.mcp or self.agents or self.instructions)

    def plugins(self) -> dict[str, list[str]]:
        """{plugin: what it provides}, from `plugin:name` skills/agents and `plugin:p:server` MCP servers."""
        out: dict[str, list[str]] = {}
        for s in self.skills:
            if ":" in s:
                p, name = s.split(":", 1)
                out.setdefault(p, []).append(f"skill {name}")
        for a in self.agents:
            if ":" in a:
                p, name = a.split(":", 1)
                out.setdefault(p, []).append(f"agent {name}")
        for m in self.mcp:
            if m.startswith("plugin:") and m.count(":") >= 2:
                _, p, name = m.split(":", 2)
                out.setdefault(p, []).append(f"mcp {name}")
        return dict(sorted(out.items()))


def _apply(lo: Loadout, a: dict) -> None:
    kind = a.get("type")
    if kind == "skill_listing":
        names = [n for n in a.get("names") or [] if isinstance(n, str)]
        lo.skills = names if a.get("isInitial") else list(dict.fromkeys(lo.skills + names))
    elif kind == "mcp_instructions_delta":
        gone = set(a.get("removedNames") or [])
        lo.mcp = [m for m in dict.fromkeys(lo.mcp + list(a.get("addedNames") or [])) if m not in gone]
    elif kind == "deferred_tools_delta":
        lo.failed_mcp = list(a.get("failedMcpServers") or [])
        lo.pending_mcp = list(a.get("pendingMcpServers") or [])
    elif kind == "agent_listing_delta":
        added, gone = list(a.get("addedTypes") or []), set(a.get("removedTypes") or [])
        base = [] if a.get("isInitial") else lo.agents
        lo.agents = [t for t in dict.fromkeys(base + added) if t not in gone]
    elif kind == "instructions":
        lo.instructions = [f["path"] for f in a.get("files") or [] if isinstance(f, dict) and f.get("path")]
    elif kind == "hook_success":
        if name := a.get("hookName"):
            lo.hooks[name] += 1
    elif kind == "invoked_skills":
        for s in a.get("skills") or []:
            path = s.get("content", "") if isinstance(s, dict) else ""
            if m := re.search(r"/plugins/cache/[^/]+/([^/]+)/([^/]+)/", path):
                lo.versions[m.group(1)] = m.group(2)


def load(path: str) -> Loadout:
    lo, commands = Loadout(), Counter()
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return lo
    with f:
        for line in f:
            # Most lines are messages; only parse the ones that can matter.
            if '"attachment"' not in line and '"Skill"' not in line and "<command-name>" not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") == "attachment" and isinstance(d.get("attachment"), dict):
                _apply(lo, d["attachment"])
                continue
            content = (d.get("message") or {}).get("content")
            if d.get("type") == "assistant" and isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "Skill":
                        if skill := (c.get("input") or {}).get("skill"):
                            lo.used[skill] += 1
            elif d.get("type") == "user" and isinstance(content, str) and (m := COMMAND.search(content)):
                commands[m.group(1).strip()] += 1
    lo.used.update({k: n for k, n in commands.items() if k in lo.skills})  # /compact etc. aren't skills
    return lo


def render(s: Session, lo: Loadout) -> Group:
    out: list = [Text(s.title, style="bold"), Text("")]
    if not lo.found:
        out.append(Text("This transcript doesn't record what was loaded (older Claude Code, or it never started).",
                        style="dim italic"))
        return Group(*out)

    def section(title: str, n: int) -> None:
        out.extend([Text(""), Text.assemble((title, "bold"), (f"  {n}", "dim"))])

    plugins = lo.plugins()
    section("Plugins", len(plugins))
    for p, parts in plugins.items():
        out.append(Text.assemble(f"  {p}", (f" {lo.versions[p]}" if p in lo.versions else "", "dim")))
        for kind in ("skill", "agent", "mcp"):
            names = [x.split(" ", 1)[1] for x in parts if x.startswith(kind + " ")]
            if not names:
                continue
            line = Text(f"      {kind}{'s' * (len(names) > 1 and kind != 'mcp')}: ", style="dim")
            for i, name in enumerate(names):
                n = lo.used.get(f"{p}:{name}", 0) if kind == "skill" else 0
                line.append((", " if i else "") + name + (f" ✓{n}×" if n else ""), "green" if n else "dim")
            out.append(line)
    own = [n for n in lo.skills if ":" not in n]
    section("Other skills", len(own))
    for name in sorted(own, key=lambda n: (-lo.used.get(n, 0), n)):
        n = lo.used.get(name, 0)
        out.append(Text.assemble(("  ✓ " if n else "    ", "green"), name,
                                 (f"  used {n}×" if n else "", "green")))
    if extra := [k for k in lo.used if k not in lo.skills]:
        out.append(Text("  used but no longer listed: " + ", ".join(sorted(extra)), style="dim"))
    section("MCP servers", len(lo.mcp) + len(lo.failed_mcp))
    for m in lo.mcp:
        out.append(Text(f"    {m}"))
    for m in lo.failed_mcp:
        out.append(Text(f"  ✗ {m}  failed to connect", style="red"))
    for m in lo.pending_mcp:
        out.append(Text(f"  … {m}  still connecting", style="dim"))
    section("Agents", len(lo.agents))
    if lo.agents:
        out.append(Text("    " + ", ".join(lo.agents)))
    section("Hooks that reported output", sum(lo.hooks.values()))
    for name, n in sorted(lo.hooks.items()):
        out.append(Text.assemble(f"    {name}", (f"  {n}×", "dim")))
    section("Instruction files", len(lo.instructions))
    for p in lo.instructions:
        out.append(Text(f"    {p}", style="dim"))
    return Group(*out)


class LoadoutView(Screen[None]):
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close", show=False),
        Binding("L", "close", "Close", show=False),
    ]
    DEFAULT_CSS = """
    LoadoutView #body { padding: 1 2; }
    """

    def __init__(self, s: Session):
        super().__init__()
        self.session = s

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="body"):
            yield Static(render(self.session, load(self.session.path)))
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#body").focus()

    def action_close(self) -> None:
        self.dismiss(None)
