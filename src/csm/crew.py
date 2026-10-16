"""A standard team for a project: subagents pinned to cheaper models, and a CLAUDE.md section
telling the main session (on Opus) when to hand work to them.

Claude Code reads project agents from <project>/.claude/agents/<name>.md; their `model` line
pins the model, whatever the session runs on. Nothing here overwrites a file: an agent that
already exists is left alone, and the CLAUDE.md section is added only once.
"""
from __future__ import annotations

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

- **scout** (Haiku): finding where something is, what calls what, how the repo does something.
- **coder** (Sonnet): a change you can specify: the files or area, the behavior, how to check it.
  Run independent pieces in parallel.
- **tester** (Haiku): running the tests, linter and type checker and reporting failures.

Keep for yourself: design decisions, bugs whose cause is unknown, and anything about auth, secrets,
pricing or production config. Review what the agents report before calling the work done. When you use
an agent that isn't pinned, such as general-purpose, pass `model` explicitly: sonnet unless the task
needs Opus.
"""


@dataclass
class Step:
    path: Path
    content: str
    action: str  # "create", "append", or "skip" (and why, in `note`)
    note: str = ""


def plan(project: str) -> list[Step]:
    """What setting up the team in `project` would do, without doing it."""
    root = Path(project)
    steps = []
    for name, text in AGENTS.items():
        path = root / ".claude" / "agents" / f"{name}.md"
        steps.append(Step(path, text, "skip", "already exists, left as it is") if path.exists() else
                     Step(path, text, "create"))
    md = root / "CLAUDE.md"
    try:
        current = md.read_text()
    except OSError:
        current = None
    if current is not None and SECTION in current:
        steps.append(Step(md, CLAUDE_MD, "skip", "already has the section"))
    else:
        steps.append(Step(md, CLAUDE_MD, "append" if current is not None else "create"))
    return steps


def apply(steps: list[Step]) -> list[Path]:
    """Carry out a plan; the files written."""
    done = []
    for st in steps:
        if st.action == "skip":
            continue
        st.path.parent.mkdir(parents=True, exist_ok=True)
        if st.action == "append":
            current = st.path.read_text()
            if SECTION in current:  # added since the plan was made
                continue
            with open(st.path, "a") as f:
                f.write(("" if current.endswith("\n") else "\n") + st.content)
        else:
            if st.path.exists():
                continue
            st.path.write_text(st.content.lstrip("\n"))
        done.append(st.path)
    return done
