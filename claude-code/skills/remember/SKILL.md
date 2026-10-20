---
name: remember
description: Store facts the user states in their StateJar memory, now rather than at the next batch.
argument-hint: "[what to remember]"
disable-model-invocation: true
allowed-tools: mcp__plugin_statejar_statejar__remember
---

Call the StateJar tool `mcp__plugin_statejar_statejar__remember` with text set to: $ARGUMENTS
and session set to "${user_config.scope}".

Report what it stored, held for a second mention, or declined, each with its reason, and the new handle. If it says
secrets were removed, say how many (never repeat them).
