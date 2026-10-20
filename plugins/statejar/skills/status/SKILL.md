---
name: status
description: Show whether StateJar memory is on, the scope, what is queued, and the current memory's handle.
disable-model-invocation: true
allowed-tools: Bash(python3 *) mcp__plugin_statejar_statejar__get
---

Local state of the StateJar plugin:

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/statejar_hook.py" status --data "${CLAUDE_PLUGIN_DATA}"`

Then call `mcp__plugin_statejar_statejar__get` with session "${user_config.scope}" and report the handle and how
many facts the current state holds. Report: paused or on, the scope, prompts queued, the last batch's time and
status, and the handle. Never print a key.
