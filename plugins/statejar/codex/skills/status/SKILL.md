---
name: status
description: Show whether StateJar memory is on for Codex, the scope, what is queued, and the current memory's handle. Use when the user asks about StateJar's status.
---

Run this from the plugin's root folder (three folders above this file) and read its JSON:

    python3 scripts/statejar_hook.py status --client codex

Then call the `get` tool of the `statejar` MCP server with `session` set to the scope it printed, and report: paused
or on, the scope, prompts queued, the last batch's time and status, the handle and how many facts the current state
holds. Never print a key.
