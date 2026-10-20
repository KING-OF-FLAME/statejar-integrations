---
name: pause
description: Pause StateJar memory for Claude Code: nothing is recalled or captured until /statejar:resume.
disable-model-invocation: true
allowed-tools: Bash(python3 *)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/statejar_hook.py" pause --data "${CLAUDE_PLUGIN_DATA}"`

Tell the user StateJar memory is paused. Prompts already queued stay queued until it resumes.
