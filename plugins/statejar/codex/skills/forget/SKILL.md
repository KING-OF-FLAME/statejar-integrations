---
name: forget
description: Remove one fact from the user's current StateJar memory. Use only when the user asks you to forget something. History keeps it; contact details are not removed by name.
---

Call the `delete` tool of the `statejar` MCP server with `fact` set to the fact the user named (for example
"budget") and `session` set to the StateJar scope named at the start of this session (`codex` by default).

Say whether a new version was stored (the fact removed from the current state) and its handle. The earlier versions
keep the fact; say so.
