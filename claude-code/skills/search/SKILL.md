---
name: search
description: Search the user's StateJar memory for what a question needs. Use when the user asks what you remember about them, or before answering a question that depends on their facts.
argument-hint: "[question]"
allowed-tools: mcp__plugin_statejar_statejar__search
---

Call the StateJar tool `mcp__plugin_statejar_statejar__search` with query set to: $ARGUMENTS
and session set to "${user_config.scope}".

Answer from its `memory_context` in plain language. Say which handle it came from (`handle_used`, shm_…) on a
line of its own at the end, so the answer can be checked. If `handle_used` is null, say there is no memory yet.
