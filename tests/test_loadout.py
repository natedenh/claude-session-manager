import json

from conftest import rec, user

from csm import loadout


def att(**a):
    return rec(type="attachment", attachment=a)


def skill_use(name):
    return rec(type="assistant", message={"role": "assistant", "content": [
        {"type": "tool_use", "name": "Skill", "input": {"skill": name}}]})


def test_loadout_follows_listings_deltas_and_uses(tmp_path):
    f = tmp_path / "s.jsonl"
    lines = [
        att(type="skill_listing", isInitial=True, names=["old"]),
        att(type="skill_listing", isInitial=True, names=["travel", "slack:standup"]),  # resumed: starts over
        att(type="skill_listing", isInitial=False, names=["browser-test:browser-test"]),
        att(type="mcp_instructions_delta", addedNames=["context7", "plugin:hive-mind:hive-mind"], removedNames=[]),
        att(type="mcp_instructions_delta", addedNames=[], removedNames=["context7"]),
        att(type="deferred_tools_delta", failedMcpServers=["slack"], pendingMcpServers=[]),
        att(type="agent_listing_delta", isInitial=True, addedTypes=["Explore", "review:critic"], removedTypes=[]),
        att(type="instructions", files=[{"path": "/u/.claude/CLAUDE.md", "type": "User"}]),
        att(type="hook_success", hookName="SessionStart:startup"),
        att(type="invoked_skills", skills=[{"name": "browser-test:browser-test",
                                            "content": "Base directory: /u/.claude/plugins/cache/acme/browser-test/1.0.0/skills/x"}]),
        skill_use("browser-test:browser-test"),
        skill_use("browser-test:browser-test"),
        rec(type="user", message={"role": "user", "content": "<command-name>/travel</command-name>"}),
        rec(type="user", message={"role": "user", "content": "<command-name>/compact</command-name>"}),
        "not json",
    ]
    f.write_text("\n".join(lines) + "\n")
    lo = loadout.load(str(f))
    assert lo.skills == ["travel", "slack:standup", "browser-test:browser-test"]
    assert lo.used == {"browser-test:browser-test": 2, "travel": 1}
    assert lo.mcp == ["plugin:hive-mind:hive-mind"] and lo.failed_mcp == ["slack"]
    assert lo.agents == ["Explore", "review:critic"] and lo.instructions == ["/u/.claude/CLAUDE.md"]
    assert lo.hooks == {"SessionStart:startup": 1} and lo.versions == {"browser-test": "1.0.0"}
    assert lo.plugins() == {"browser-test": ["skill browser-test"], "hive-mind": ["mcp hive-mind"],
                            "review": ["agent critic"], "slack": ["skill standup"]}


def test_loadout_of_transcript_without_listings(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text(user("hi", "/tmp") + "\n")
    assert not loadout.load(str(f)).found
    assert not loadout.load(str(tmp_path / "missing.jsonl")).found
