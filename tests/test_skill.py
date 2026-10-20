"""The portable agent skill (skills/statejar/SKILL.md) follows the Agent Skills format the clients validate
(OpenCode 1.19's rules: only name, description, license, compatibility and metadata; name lowercase with single
hyphens, 1-64 characters, equal to its folder), and names only CLI commands the statejar CLI has."""

from __future__ import annotations

import re

from conftest import ROOT

SKILL = ROOT / "skills" / "statejar" / "SKILL.md"
CLI_COMMANDS = {"init", "add", "search", "list", "forget", "export", "status", "login", "logout", "whoami"}


def frontmatter() -> tuple[dict[str, str], str]:
    text = SKILL.read_text()
    m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
    assert m, "SKILL.md starts with YAML frontmatter"
    fields, current = {}, None
    for line in m.group(1).splitlines():
        if re.match(r"^[a-z]+:", line):
            current, _, value = line.partition(":")
            fields[current] = value.strip()
        elif current:
            fields[current] += "\n" + line
    return fields, m.group(2)


def test_the_frontmatter_has_only_the_recognised_fields():
    fields, _ = frontmatter()
    assert set(fields) <= {"name", "description", "license", "compatibility", "metadata"}
    assert fields["name"] == SKILL.parent.name == "statejar"
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", fields["name"]) and len(fields["name"]) <= 64
    assert 0 < len(fields["description"]) <= 1024


def test_it_names_only_commands_the_cli_has_and_warns_about_secrets():
    _, body = frontmatter()
    used = set(re.findall(r"`statejar ([a-z]+)", body))
    assert used and used <= CLI_COMMANDS
    assert "Never store secrets" in body
