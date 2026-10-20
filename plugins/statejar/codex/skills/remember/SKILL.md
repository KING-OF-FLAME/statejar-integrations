---
name: remember
description: Store facts the user states in their StateJar memory now, rather than at the next batch. Use only when the user asks you to remember something.
---

Call the `remember` tool of the `statejar` MCP server with `text` set to what the user asked you to remember, in
their words, and `session` set to the StateJar scope named at the start of this session (`codex` by default).

Report what it stored, held for a second mention, or declined, each with its reason, and the new handle. If it says
secrets were removed, say how many (never repeat them).
