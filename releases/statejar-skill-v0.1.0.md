# StateJar agent skill 0.1.0

`skills/statejar/SKILL.md`, an [Agent Skill](https://agentskills.io) that tells an agent when and how to remember and
recall the user's own facts with StateJar: search before answering, store the user's own words, never store secrets,
and say that forgetting keeps history. It uses StateJar's MCP tools (`search`, `remember`, `get`, `list`, `update`,
`delete`, `history`, `audit`, `verify`, `export`) from `https://statejar.com/mcp`, live since 2026-10-21, or, without
them, the `statejar` CLI (`add`, `search`, `list`, `forget`, `export`, `status`, `init`), which needs `statejar` 0.7.0
or later from PyPI.

```bash
git clone https://github.com/KING-OF-FLAME/statejar-integrations
mkdir -p ~/.claude/skills && cp -r statejar-integrations/skills/statejar ~/.claude/skills/   # Claude Code
claude mcp add --transport http statejar https://statejar.com/mcp --header "Authorization: Bearer $STATEJAR_API_KEY"
```

For other agents' skill folders (OpenCode, OpenClaw, Codex, Hermes Agent), see `skills/README.md`.

**End-to-end test (2026-10-27, `e2e/skill_e2e.py`):** Claude Code 2.1.311 with the skill copied into
`~/.claude/skills/` and no plugin, against a local StateJar server running the hosted server's code (main 75a67c7),
with a scripted stand-in model (`e2e/scripted_model.py --skill`). On every session's first request the stand-in
checked that Claude Code had given it the skill's name and description and the Skill tool. It then invoked the skill
and checked that the skill's instructions arrived, and only after that did what they say. MCP route: `remember` in
session 1 stored the facts, and `search` in a fresh session 2 recalled the budget with its handle. The recall was in
the audit trail. CLI route, with no MCP server and the built `statejar` 0.7.0: `statejar add` in one session, then
`statejar search` in a fresh one. 12 of 12 checks passed, and both throwaway accounts were deleted. CI runs the
discovery part on every change against Claude Code 2.1.311, with no StateJar (`--discovery-only`).

Licence: MIT.
