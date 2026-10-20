---
name: statejar
description: Remember and recall the user's own facts with StateJar (name, city, budget, preferences, plans, constraints). Use before answering a question that depends on the user's facts, when the user tells you something about themselves worth keeping, or when they ask you to remember, forget, list or export what you know about them.
license: LicenseRef-StateJar-SDK-License
compatibility: Needs StateJar's MCP server (https://statejar.com/mcp) or the statejar CLI (pip install statejar), and a StateJar developer key.
metadata:
  homepage: https://statejar.com/docs/skills
  version: "0.1.0"
---

# StateJar memory

StateJar keeps a user's facts as one versioned state per scope: one value per field, each checked by its type, every
change a new version with the old value kept in history. Each state has a handle (`shm_…`, the SHA-256 of the canonical
state), so any answer you give from it can be checked. Every read is written to the user's audit trail.

## Which way to call it

1. If you have StateJar's MCP tools (`search`, `remember`, `get`, `list`, `update`, `delete`, `history`, `audit`,
   `verify`, `export`, sometimes prefixed with the server's name), use them.
2. Otherwise use the `statejar` command, signed in with `statejar init` or with `STATEJAR_API_KEY` set:

| to | run |
|---|---|
| answer from memory | `statejar search "<the user's question>"` |
| keep what the user said | `statejar add "<the user's words>"` |
| show everything | `statejar list` |
| remove one fact | `statejar forget <fact>` (for example `statejar forget budget`) |
| export | `statejar export --out statejar-export.json` |
| check the connection | `statejar status` |

Add `--scope <name>` to work in one scope (a project, an agent, a chat). Without it the command uses `STATEJAR_SCOPE`,
then the scope `statejar init` saved, then `default`.

## How to use it well

- **Search before you answer** a question that depends on the user's facts. Use only what the search returns, and say
  which handle it came from when the user may want to check.
- **Store the user's own words**, not your summary. StateJar extracts the facts, refuses a value that fails its type
  (with a reason), and holds a fact said only once until it is said again. Report what was stored, held or declined.
- **Never store secrets**: no passwords, API keys, tokens, private keys or card numbers, even when the user includes
  them. Leave them out of what you send.
- **Don't store** other people's private details, questions, hypotheticals or things the user only plans or considers.
- **Forgetting keeps history.** `forget` makes a new version without the fact; earlier versions still have it. Say so.
- If StateJar answers 401, the key is wrong or revoked: ask the user to run `statejar init` again. If it answers 429,
  the plan's limit was reached: tell the user and carry on without memory.
