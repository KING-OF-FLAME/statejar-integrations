# The StateJar agent skill

`statejar/SKILL.md` is an [Agent Skill](https://agentskills.io): plain instructions any agent that reads skills can
follow to remember and recall the user's facts with StateJar, through StateJar's MCP tools or the `statejar` CLI
(`pip install statejar`).

## Install

Copy the `statejar` folder to where your agent reads skills:

| agent | folder |
|---|---|
| Claude Code | `~/.claude/skills/statejar/` (or use the plugin in `plugins/statejar`, which includes its own skills) |
| OpenCode | `~/.config/opencode/skills/statejar/` or `~/.agents/skills/statejar/` |
| OpenClaw | `~/.agents/skills/statejar/`, or `<workspace>/skills/statejar/` for one workspace |
| Codex | `$CODEX_HOME/skills/statejar/` (`~/.codex/skills/statejar/`) |
| Hermes Agent | `~/.agents/skills/statejar/`, with `~/.agents/skills` under `skills.external_dirs` in `~/.hermes/config.yaml` |

```bash
git clone https://github.com/KING-OF-FLAME/statejar-integrations
mkdir -p ~/.agents/skills && cp -r statejar-integrations/skills/statejar ~/.agents/skills/
pip install statejar && statejar init     # sign in and choose a default scope
```

The skill follows the Agent Skills format: a `SKILL.md` whose frontmatter has `name` (matching its folder),
`description`, `license`, `compatibility` and `metadata`, and nothing else.
