---
name: search
description: Search the user's StateJar memory for what a question needs. Use when the user asks what you remember about them, or before answering a question that depends on their facts (their name, city, budget, preferences, plans).
---

Call the `search` tool of the `statejar` MCP server with `query` set to the user's question and `session` set to the
StateJar scope named at the start of this session (`codex` unless the user set STATEJAR_SCOPE).

Answer from its `memory_context` in plain language. Say which handle it came from (`handle_used`, shm_…) on a line of
its own at the end, so the answer can be checked. If `handle_used` is null, say there is no memory yet.
