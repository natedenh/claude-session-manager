"""A standard team for a project: subagents pinned to cheaper models, and a CLAUDE.md section
telling the main session (on Opus) when to hand work to them.

Claude Code reads project agents from <project>/.claude/agents/<name>.md; their `model` line
pins the model, whatever the session runs on. Nothing hand-edited is overwritten: an agent file
or CLAUDE.md section that is an unchanged copy of an earlier csm version gets updated, anything
else that exists is left alone, and the CLAUDE.md section is added only once.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

SECTION = "## Delegating to subagents"


def _agent(name: str, model: str, description: str, body: str, tools: str = "") -> str:
    return (f"---\nname: {name}\ndescription: {description}\nmodel: {model}\n" + (f"tools: {tools}\n" if tools else "")
            + f"---\n\n{body.strip()}\n")


AGENTS = {
    "scout": _agent(
        "scout", "haiku",
        "Read-only search. Use to find where something is handled, what calls what, or how this repo does "
        "something, when only the conclusion is needed. Say how thorough to be. Never edits.",
        """
You find things in this codebase and report where they are. You never modify anything. Use Bash only
for read-only commands: rg, find, ls, git log, git grep, git show, git blame.

Try the repo's naming conventions and synonyms, and look at definitions, call sites, config, tests and
docs. Read excerpts, not whole files. Stop once the answer is solid unless asked to be thorough.

Report back with the answer first, then the supporting locations as path:line with a one-line note
each, then anything you couldn't resolve. Don't paste large blocks of code.
""", tools="Read, Grep, Glob, Bash"),
    "coder": _agent(
        "coder", "sonnet",
        "Implements a change the caller has specified: which files or area, the behavior wanted, and how to "
        "check it. Use for well-defined features, fixes with a known cause, refactors and mechanical edits. "
        "Not for design decisions or bugs whose cause is unknown.",
        """
You implement the change the caller described, and nothing beyond it.

Read the code you're changing and match its style, naming and comment density. If the spec turns out
to be ambiguous or wrong once you're in the code, stop and report what you found rather than guessing.

When the change is made, run the checks the caller named, or the project's tests, linter and type
checker for the area you touched. Fix what your change broke; report failures that were already there.

Don't commit unless asked. Report back with the files you changed, what you ran and its result, and
anything you skipped or weren't sure about.
"""),
    "tester": _agent(
        "tester", "haiku",
        "Runs the project's tests, linter and type checker and reports what failed, briefly. Use after a "
        "change, or to check the baseline before one. Never edits.",
        """
You run checks and report results. You don't change any files.

Find how this project runs its tests, linter and type checker (README, CONTRIBUTING, package scripts,
Makefile, CI config). Run what the caller asked for, or all of them for the area named.

Report back with each command you ran and whether it passed. For each failure, give the test or file,
the assertion or error in a line or two, and path:line where it points. Don't guess at fixes.
""", tools="Read, Grep, Glob, Bash"),
}

CLAUDE_MD = f"""
{SECTION}

Plan and review the work yourself; hand the doing to this project's subagents, which run on cheaper
models (they're in .claude/agents):

- **scout** (Haiku): searches across many files: where something is handled, what calls what, how the
  repo does something. When you already know the file or symbol, read or grep it yourself; that's
  quicker than a round trip.
- **coder** (Sonnet): a change you can specify. Name the files and lines or the area, the behavior
  wanted and its edge cases, the tests to add, and the commands to run; the tighter the spec, the
  faster and surer the result. It runs the tests for what it touched. Run independent pieces in
  parallel; when their files might overlap, give each `isolation: "worktree"` so they don't collide.
- **tester** (Haiku): running the linter, type checker and tests while the work is in progress, and
  reporting failures.

Keep for yourself: design decisions, bugs whose cause is unknown, and anything about auth, secrets,
pricing or production config. Review what the agents report before calling the work done: read the diff,
and before committing run the full test suite yourself (in the background if it's slow) rather than
relying on a summary. When you use an agent that isn't pinned, such as general-purpose, pass `model`
explicitly: sonnet unless the task needs Opus.
"""


def digest(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()[:16]


# Digests of every earlier version csm has written. When you change an agent's text or CLAUDE_MD, add
# the old digest here so projects holding the old copy get updated.
EARLIER = {
    "scout": set(),
    "coder": set(),
    "tester": {"788c6f2fa4acc2be"},          # before it had a tools line
    "CLAUDE.md": {"2b3838e269801421",        # the first delegation section
                  "5983a329b56624fb"},       # before the worktree advice
}


@dataclass
class Step:
    path: Path
    content: str
    action: str  # "create", "append", "update" or "skip" (and why, in `note`)
    note: str = ""
    old: str = ""  # the text being replaced, or the hand-edited text left alone (for showing a diff)


_UNREADABLE = "\0unreadable"  # what _read returns for a file that exists but can't be read


def _read(path: Path) -> str | None:
    """The file's text; None if it's missing, _UNREADABLE if it can't be read or decoded."""
    try:
        return path.read_text()
    except (OSError, UnicodeDecodeError):
        return None if not path.exists() else _UNREADABLE


def section_span(text: str) -> tuple[int, int] | None:
    """Where csm's CLAUDE.md section is: from SECTION at the start of a line to the next level-2 heading."""
    if text.startswith(SECTION):
        start = 0
    else:
        i = text.find("\n" + SECTION)
        if i < 0:
            return None
        start = i + 1
    end = text.find("\n## ", start + len(SECTION))
    return start, len(text) if end < 0 else end


def plan(project: str) -> list[Step]:
    """What setting up or updating the team in `project` would do, without doing it."""
    root = Path(project)
    steps = []
    for name, text in AGENTS.items():
        path = root / ".claude" / "agents" / f"{name}.md"
        current = _read(path)
        if current is None:
            steps.append(Step(path, text, "create"))
        elif current == _UNREADABLE:
            steps.append(Step(path, text, "skip", "couldn't read it, left as it is"))
        elif digest(current) == digest(text):
            steps.append(Step(path, text, "skip", "up to date"))
        elif digest(current) in EARLIER[name]:
            steps.append(Step(path, text, "update", "an earlier csm version", old=current))
        else:
            steps.append(Step(path, text, "skip", "edited by hand, left as it is", old=current))
    md = root / "CLAUDE.md"
    current = _read(md)
    if current is None:
        steps.append(Step(md, CLAUDE_MD, "create"))
    elif current == _UNREADABLE:
        steps.append(Step(md, CLAUDE_MD, "skip", "couldn't read it, left as it is"))
    elif (span := section_span(current)) is None:
        steps.append(Step(md, CLAUDE_MD, "append"))
    else:
        old = current[span[0]:span[1]]
        if digest(old) == digest(CLAUDE_MD):
            steps.append(Step(md, CLAUDE_MD, "skip", "up to date"))
        elif digest(old) in EARLIER["CLAUDE.md"]:
            steps.append(Step(md, CLAUDE_MD, "update", "an earlier csm version", old=old))
        else:
            steps.append(Step(md, CLAUDE_MD, "skip", "section edited by hand, left as it is", old=old))
    return steps


def apply(steps: list[Step]) -> list[Step]:
    """Carry out a plan; the steps actually done (a file that changed since the plan is skipped)."""
    done = []
    for st in steps:
        if st.action == "skip":
            continue
        is_md = st.path.name == "CLAUDE.md"
        if st.action == "update":
            current = _read(st.path)
            if current is None or current == _UNREADABLE:
                continue
            if is_md:
                span = section_span(current)
                if span is None or digest(current[span[0]:span[1]]) != digest(st.old):
                    continue
                start, end = span
                st.path.write_text(current[:start] + CLAUDE_MD.strip()
                                   + ("\n" + current[end:] if end < len(current) else "\n"))
            else:
                if digest(current) != digest(st.old):
                    continue
                st.path.write_text(st.content.lstrip("\n"))
        elif st.action == "append":
            current = _read(st.path)
            if current is None or current == _UNREADABLE or SECTION in current:  # gone, unreadable, or added since
                continue
            with open(st.path, "a") as f:
                f.write(("" if current.endswith("\n") else "\n") + st.content)
        else:
            if st.path.exists():
                continue
            st.path.parent.mkdir(parents=True, exist_ok=True)
            st.path.write_text(st.content.lstrip("\n"))
        done.append(st)
    return done
