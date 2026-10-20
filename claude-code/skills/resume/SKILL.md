---
name: resume
description: Resume StateJar memory for Claude Code after /statejar:pause.
disable-model-invocation: true
allowed-tools: Bash(python3 *)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/statejar_hook.py" resume --data "${CLAUDE_PLUGIN_DATA}"`

Tell the user StateJar memory is on again: recall at the next session start or prompt, capture from the next prompt.
